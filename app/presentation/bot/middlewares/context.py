"""Outer middlewares: per-user serialization, DB session + services + current user, error handling."""

import asyncio
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message, TelegramObject, Update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.dto.messaging import Messenger
from app.application.services.container import Services
from app.core.config.policy import Policy
from app.core.exceptions import AppError
from app.core.logging import get_logger, new_correlation_id
from app.core.utils.clock import TimeService

log = get_logger(__name__)

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]

GENERIC_ERROR = "⚠️ خطای غیرمنتظره‌ای رخ داد. لطفاً چند لحظه بعد دوباره تلاش کنید."
ALERT_LIMIT = 190


def _from_user_id(update: Update) -> int | None:
    for obj in (update.message, update.callback_query, update.edited_message):
        if obj is not None and obj.from_user is not None:
            return obj.from_user.id
    return None


class UserLockMiddleware(BaseMiddleware):
    """Processes one update at a time per user and drops message floods.

    The bot runs as a single polling process, so an in-process lock is enough to stop a double
    tap from running two handlers concurrently for the same person.
    """

    def __init__(self, min_interval_seconds: float):
        self.min_interval = min_interval_seconds
        self.locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.last_message: dict[int, float] = {}

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        assert isinstance(event, Update)
        user_id = _from_user_id(event)
        if user_id is None:
            return await handler(event, data)
        if event.message is not None and self.min_interval:
            now = time.monotonic()
            if now - self.last_message.get(user_id, 0.0) < self.min_interval:
                log.info("throttled message", user=user_id)
                return None
            self.last_message[user_id] = now
        async with self.locks[user_id]:
            return await handler(event, data)


class ContextMiddleware(BaseMiddleware):
    """Opens a DB session per update and injects `services`, `user` and `policy` into handlers."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        policy: Policy,
        time_service: TimeService,
        messenger: Messenger,
        bootstrap_ids: frozenset[int],
    ):
        self.session_factory = session_factory
        self.policy = policy
        self.time = time_service
        self.messenger = messenger
        self.bootstrap_ids = bootstrap_ids

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        assert isinstance(event, Update)
        cid = new_correlation_id()
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(correlation_id=cid, update_id=event.update_id)
        async with self.session_factory() as session:
            services = Services(session, self.policy, self.time, self.messenger, self.bootstrap_ids)
            user = None
            tg_user = data.get("event_from_user")
            if tg_user is not None:
                user = await services.users.get_by_messenger_id(tg_user.id)
                if user is not None:
                    user = await services.users.ensure_bootstrap_roles(user)
                    chat = data.get("event_chat")
                    if chat is not None and chat.type == "private":
                        await services.users.touch_chat(user, chat.id, tg_user.username)
            data["services"] = services
            data["user"] = user
            data["policy"] = self.policy
            try:
                return await handler(event, data)
            finally:
                # Anything a handler left uncommitted is discarded on purpose.
                await session.rollback()


async def respond(update: Update, text: str) -> None:
    """Show an error to the user on whichever kind of update caused it."""
    if update.callback_query is not None:
        query: CallbackQuery = update.callback_query
        if len(text) <= ALERT_LIMIT:
            await query.answer(text, show_alert=True)
            return
        await query.answer()
        if query.message is not None:
            await query.message.answer(text)
        return
    message: Message | None = update.message
    if message is not None:
        await message.answer(text)


class ErrorMiddleware(BaseMiddleware):
    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        assert isinstance(event, Update)
        try:
            return await handler(event, data)
        except AppError as exc:
            log.info("user-facing error", error_type=type(exc).__name__, detail=str(exc))
            await self._safe_respond(event, exc.user_message)
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc):
                if event.callback_query is not None:
                    await event.callback_query.answer()
                return None
            log.exception("telegram bad request")
            await self._safe_respond(event, GENERIC_ERROR)
        except Exception:
            log.exception("unhandled error")
            await self._safe_respond(event, GENERIC_ERROR)
        return None

    @staticmethod
    async def _safe_respond(update: Update, text: str) -> None:
        try:
            await respond(update, text)
        except Exception:
            log.exception("could not deliver error message")
