from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.security.rbac import ROLE_TITLES_FA, RoleCode
from app.domain.users.models import EmployeeRosterEntry, Role, User, UserRole
from app.infrastructure.repositories.base import Repository


class RosterRepository(Repository[EmployeeRosterEntry]):
    model = EmployeeRosterEntry

    async def get_by_code(self, code: str, for_update: bool = False) -> EmployeeRosterEntry | None:
        stmt = select(EmployeeRosterEntry).where(EmployeeRosterEntry.code == code)
        if for_update:
            stmt = stmt.with_for_update()
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_user(self, user_id: UUID) -> EmployeeRosterEntry | None:
        stmt = select(EmployeeRosterEntry).where(EmployeeRosterEntry.user_id == user_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_all(self) -> list[EmployeeRosterEntry]:
        stmt = select(EmployeeRosterEntry).order_by(EmployeeRosterEntry.full_name, EmployeeRosterEntry.code)
        return list((await self.session.execute(stmt)).scalars())


class UserRepository(Repository[User]):
    model = User

    async def get_by_messenger_id(self, messenger_user_id: int) -> User | None:
        stmt = select(User).where(User.messenger_user_id == messenger_user_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_active(self) -> list[User]:
        stmt = select(User).where(User.is_active.is_(True)).order_by(User.full_name)
        return list((await self.session.execute(stmt)).scalars())

    async def list_page(self, offset: int, limit: int) -> tuple[list[User], int]:
        total = (await self.session.execute(select(func.count(User.id)))).scalar_one()
        stmt = select(User).order_by(User.is_active.desc(), User.full_name).offset(offset).limit(limit)
        return list((await self.session.execute(stmt)).scalars()), total

    async def list_active_with_role(self, role: RoleCode) -> list[User]:
        stmt = (
            select(User)
            .join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(Role.code == role, User.is_active.is_(True))
            .options(selectinload(User.role_links))
            .order_by(User.full_name)
        )
        return list((await self.session.execute(stmt)).scalars().unique())

    async def list_active_employees(self) -> list[User]:
        return await self.list_active_with_role(RoleCode.EMPLOYEE)

    async def get_many(self, ids: set[UUID]) -> dict[UUID, User]:
        if not ids:
            return {}
        stmt = select(User).where(User.id.in_(ids))
        return {u.id: u for u in (await self.session.execute(stmt)).scalars()}


class RoleRepository(Repository[Role]):
    model = Role

    async def get_by_code(self, code: RoleCode) -> Role:
        stmt = select(Role).where(Role.code == code)
        role = (await self.session.execute(stmt)).scalar_one_or_none()
        if role is None:
            # Roles are seeded by the initial migration; this keeps fresh test databases working.
            role = Role(code=code, title=ROLE_TITLES_FA[code])
            self.session.add(role)
            await self.session.flush()
        return role

    async def find_link(self, user_id: UUID, role_id: UUID) -> UserRole | None:
        stmt = select(UserRole).where(UserRole.user_id == user_id, UserRole.role_id == role_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()
