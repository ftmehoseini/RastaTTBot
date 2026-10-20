"""Builds the aiogram Dispatcher: storage, middlewares and routers in priority order."""

from aiogram import Dispatcher
from aiogram.fsm.storage.base import BaseStorage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.dto.messaging import Messenger
from app.core.config.policy import Policy
from app.core.utils.clock import TimeService
from app.presentation.bot.handlers import (
    admin,
    approvals,
    attendance,
    common,
    fallback,
    food,
    food_admin,
    leave,
    start,
)
from app.presentation.bot.middlewares.context import ContextMiddleware, ErrorMiddleware, UserLockMiddleware

# Order matters: the first router whose filters match handles the update.
# start (registration, /cancel) first, generic pickers before modules, fallback last.
ROUTERS = [
    start.router,
    common.router,
    approvals.router,
    attendance.router,
    leave.router,
    food_admin.router,
    food.router,
    admin.router,
    fallback.router,
]


def build_dispatcher(
    storage: BaseStorage,
    session_factory: async_sessionmaker[AsyncSession],
    policy: Policy,
    time_service: TimeService,
    messenger: Messenger,
    bootstrap_ids: frozenset[int],
) -> Dispatcher:
    dp = Dispatcher(storage=storage)
    dp.update.outer_middleware(UserLockMiddleware(policy.rate_limit.min_interval_seconds))
    dp.update.outer_middleware(ContextMiddleware(session_factory, policy, time_service, messenger, bootstrap_ids))
    dp.update.outer_middleware(ErrorMiddleware())
    for router in ROUTERS:
        # Routers are module-level singletons; detach them so a dispatcher can be rebuilt (tests).
        router._parent_router = None
        dp.include_router(router)
    return dp
