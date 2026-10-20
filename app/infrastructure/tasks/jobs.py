"""Celery tasks. Each task runs one asyncio unit of work with its own engine, bot session and lock."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import UUID

from redis import Redis
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

import app.domain.models  # noqa: F401  (registers all ORM models)
from app.application.services.container import Services
from app.application.use_cases.food_broadcast import broadcast_menu
from app.application.use_cases.reminders import send_check_in_reminders, send_check_out_reminders
from app.core.config import get_policy, get_settings
from app.core.database import create_session_factory
from app.core.logging import configure_logging, get_logger, new_correlation_id
from app.core.utils.clock import SystemClock, TimeService
from app.infrastructure.integrations.messenger import AiogramMessenger, create_bot
from app.infrastructure.tasks.celery_app import celery_app

log = get_logger(__name__)

settings = get_settings()
configure_logging(settings.log_level, settings.log_format)


@asynccontextmanager
async def unit_of_work():
    policy = get_policy()
    # NullPool: every task runs in a fresh event loop, so pooled connections can't be reused.
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    bot = create_bot(settings)
    try:
        async with create_session_factory(engine)() as session:
            yield Services(
                session,
                policy,
                TimeService(SystemClock(), policy.organization.timezone),
                AiogramMessenger(bot, policy.notifications.max_send_retries),
            )
    finally:
        await bot.session.close()
        await engine.dispose()


def run_exclusive[T](lock_name: str, job: Callable[[Services], Awaitable[T]], timeout: int = 840) -> T | None:
    """Run `job` unless another worker holds the lock (overlapping Beat ticks, retries)."""
    new_correlation_id()
    redis = Redis.from_url(settings.redis_url)
    lock = redis.lock(f"lock:{lock_name}", timeout=timeout, blocking=False)
    if not lock.acquire():
        log.info("job already running elsewhere", job=lock_name)
        return None
    try:

        async def runner() -> T:
            async with unit_of_work() as services:
                return await job(services)

        return asyncio.run(runner())
    finally:
        try:
            lock.release()
        except Exception:
            log.warning("lock already expired", job=lock_name)
        redis.close()


def _summary(result: Any) -> dict[str, Any]:
    return result.__dict__ if hasattr(result, "__dict__") else {"result": result}


@celery_app.task(name="attendance.check_in_reminders")
def check_in_reminders() -> dict[str, Any] | None:
    result = run_exclusive("attendance.check_in_reminders", send_check_in_reminders)
    return _summary(result) if result else None


@celery_app.task(name="attendance.check_out_reminders")
def check_out_reminders() -> dict[str, Any] | None:
    result = run_exclusive("attendance.check_out_reminders", send_check_out_reminders)
    return _summary(result) if result else None


@celery_app.task(
    name="food.broadcast_menu",
    autoretry_for=(ConnectionError, OSError),
    retry_backoff=True,
    max_retries=3,
)
def broadcast_menu_task(actor_id: str, menu_id: str) -> dict[str, Any] | None:
    async def job(services: Services):
        return await broadcast_menu(services, UUID(actor_id), UUID(menu_id))

    # Per-menu lock: a second click while sending waits for the next run; re-runs resume safely.
    result = run_exclusive(f"food.broadcast_menu:{menu_id}", job, timeout=3600)
    if result is not None:
        actor_id_uuid = UUID(actor_id)

        async def report() -> None:
            async with unit_of_work() as services:
                actor = await services.users.get(actor_id_uuid)
                await services.notifications.notify_user(
                    actor,
                    f"📣 اطلاع‌رسانی منو انجام شد.\nارسال‌شده: {result.sent} | ناموفق: {result.failed}"
                    f" | قبلاً ارسال‌شده: {result.skipped}",
                )

        asyncio.run(report())
    return _summary(result) if result else None


@celery_app.task(name="maintenance.cleanup_files")
def cleanup_files() -> int:
    """Delete temporary reports and uploads older than the retention window.

    Only top-level files are removed; retained menu files live in `<UPLOAD_DIR>/menus/`.
    """
    cutoff = time.time() - get_policy().reports.retention_hours * 3600
    removed = 0
    for directory in (settings.reports_dir, settings.upload_dir):
        path = Path(directory)
        if not path.is_dir():
            continue
        for file in path.iterdir():
            if file.is_file() and file.stat().st_mtime < cutoff:
                file.unlink(missing_ok=True)
                removed += 1
    log.info("cleanup finished", removed=removed)
    return removed
