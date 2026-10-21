"""Bot process entry point: `python -m app.presentation.bot.main`.

Runs long polling plus the HTTP health endpoint, and shuts down gracefully on SIGTERM/SIGINT
(aiogram stops polling, then the DB pool, Redis and HTTP sessions are closed).
"""

import asyncio

from aiogram.fsm.storage.base import BaseStorage
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage
from redis.asyncio import Redis

import app.domain.models  # noqa: F401  (registers all ORM models)
from app.core.config import get_policy, get_settings
from app.core.database import create_engine, create_session_factory
from app.core.logging import configure_logging, get_logger
from app.core.utils.clock import SystemClock, TimeService
from app.infrastructure.integrations.messenger import AiogramMessenger, create_bot
from app.presentation.api.health import set_redis, start_health_server
from app.presentation.bot.dispatcher import build_dispatcher

log = get_logger("bot")


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    policy = get_policy()
    if not settings.bot_token.get_secret_value():
        raise SystemExit("BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")

    engine = create_engine(settings.database_url, settings.database_echo)
    session_factory = create_session_factory(engine)
    redis: Redis | None = None
    storage: BaseStorage
    if settings.redis_url:
        redis = Redis.from_url(settings.redis_url)
        storage = RedisStorage(redis, state_ttl=86400, data_ttl=86400)
    else:
        log.warning("REDIS_URL empty: using in-memory FSM storage (development only)")
        storage = MemoryStorage()
    set_redis(redis)

    bot = create_bot(settings)
    messenger = AiogramMessenger(bot, policy.notifications.max_send_retries)
    time_service = TimeService(SystemClock(), policy.organization.timezone)
    dp = build_dispatcher(storage, session_factory, policy, time_service, messenger, settings.super_admin_ids)
    health = await start_health_server(engine, settings.health_host, settings.health_port)

    me = await bot.get_me()
    log.info("bot started", username=me.username, platform=settings.bot_platform.value, api=settings.api_base_url)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types(), handle_signals=True)
    finally:
        log.info("shutting down")
        await health.cleanup()
        await storage.close()
        await bot.session.close()
        await engine.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
