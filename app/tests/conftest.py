"""Test fixtures.

Tests run against SQLite in memory by default. Set TEST_DATABASE_URL to a PostgreSQL URL to run
the same tests against PostgreSQL (row locks, identity columns and timestamptz behave for real):

    TEST_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/rasta_bot_test pytest
"""

import os
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

import app.domain.models  # noqa: F401
from app.application.dto.messaging import DeliveryError, InlineButtons
from app.application.services.container import Services
from app.core.config.policy import Policy, load_policy
from app.core.database import Base, create_engine, create_session_factory
from app.core.security.rbac import RoleCode
from app.core.utils.clock import FixedClock, TimeService
from app.domain.leave.models import LeaveRequest
from app.domain.users.models import User

TEHRAN = ZoneInfo("Asia/Tehran")
ROOT = Path(__file__).resolve().parents[2]


def tehran(*args: int) -> datetime:
    return datetime(*args, tzinfo=TEHRAN)


class FakeMessenger:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, InlineButtons | None]] = []
        self.documents: list[tuple[int, str, bytes, str | None]] = []
        self.fail_chats: set[int] = set()

    async def send_text(self, chat_id: int, text: str, buttons: InlineButtons | None = None) -> None:
        if chat_id in self.fail_chats:
            raise DeliveryError("blocked", permanent=True)
        self.sent.append((chat_id, text, buttons))

    async def send_document(self, chat_id: int, filename: str, content: bytes, caption: str | None = None) -> None:
        self.documents.append((chat_id, filename, content, caption))

    def texts_to(self, chat_id: int) -> list[str]:
        return [text for cid, text, _ in self.sent if cid == chat_id]


@pytest.fixture
def policy() -> Policy:
    return load_policy(ROOT / "config")


@pytest.fixture
def clock() -> FixedClock:
    # Monday 2026-10-19 = دوشنبه ۲۷ مهر ۱۴۰۵, 08:00 Tehran
    return FixedClock(tehran(2026, 10, 19, 8, 0))


@pytest.fixture
def messenger() -> FakeMessenger:
    return FakeMessenger()


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    url = os.environ.get("TEST_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    db_engine = create_engine(url)
    if url.startswith("sqlite"):
        # SQLite has no identity columns; PostgreSQL generates leave numbers.
        LeaveRequest.__table__.c.number.nullable = True
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield db_engine
    await db_engine.dispose()


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


@pytest.fixture
async def session(session_factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with session_factory() as s:
        yield s


@pytest.fixture
def services(session: AsyncSession, policy: Policy, clock: FixedClock, messenger: FakeMessenger) -> Services:
    return Services(session, policy, TimeService(clock, policy.organization.timezone), messenger)


class UserFactory:
    def __init__(self, services: Services):
        self.services = services
        self.counter = 1000

    async def __call__(self, name: str = "کاربر آزمایشی", *roles: RoleCode, active: bool = True) -> User:
        self.counter += 1
        user = await self.services.users.register(self.counter, self.counter, name, None)
        for role in roles:
            if role != RoleCode.EMPLOYEE:
                await self.services.users._link_role(user, role, assigned_by=None)
        if not active:
            user.is_active = False
        await self.services.session.commit()
        await self.services.session.refresh(user, ["role_links"])
        return user


@pytest.fixture
def make_user(services: Services) -> UserFactory:
    return UserFactory(services)
