from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services import access
from app.core.config.policy import UsersPolicy
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.security.rbac import Permission, RoleCode
from app.core.utils.clock import TimeService
from app.core.utils.text import clean_text
from app.domain.audit.models import AuditAction
from app.domain.users.models import User, UserRole
from app.infrastructure.repositories.audit import AuditRepository
from app.infrastructure.repositories.users import RoleRepository, UserRepository


class UserService:
    def __init__(
        self,
        session: AsyncSession,
        policy: UsersPolicy,
        time: TimeService,
        bootstrap_super_admin_ids: frozenset[int] = frozenset(),
    ):
        self.session = session
        self.policy = policy
        self.time = time
        self.bootstrap_ids = bootstrap_super_admin_ids
        self.users = UserRepository(session)
        self.roles = RoleRepository(session)
        self.audit = AuditRepository(session)

    async def get_by_messenger_id(self, messenger_user_id: int) -> User | None:
        return await self.users.get_by_messenger_id(messenger_user_id)

    async def get(self, user_id: UUID) -> User:
        user = await self.users.get(user_id)
        if user is None:
            raise NotFoundError("کاربر پیدا نشد.")
        return user

    def validate_full_name(self, text: str) -> str:
        name = clean_text(text, self.policy.max_full_name_length)
        if len(name) < self.policy.min_full_name_length or name.startswith("/") or any(c.isdigit() for c in name):
            raise ValidationError("لطفاً نام و نام خانوادگی را به‌درستی و فقط با حروف بنویسید.")
        return name

    async def register(self, messenger_user_id: int, chat_id: int, full_name: str, username: str | None) -> User:
        name = self.validate_full_name(full_name)
        if await self.users.get_by_messenger_id(messenger_user_id):
            raise ConflictError("شما قبلاً ثبت‌نام کرده‌اید.")
        is_bootstrap_admin = messenger_user_id in self.bootstrap_ids
        user = self.users.add(
            User(
                messenger_user_id=messenger_user_id,
                chat_id=chat_id,
                full_name=name,
                username=username,
                is_active=is_bootstrap_admin or not self.policy.require_admin_activation,
                registered_at=self.time.now_utc(),
                role_links=[],
            )
        )
        await self.session.flush()
        await self._link_role(user, RoleCode.EMPLOYEE, assigned_by=None)
        if is_bootstrap_admin:
            await self._link_role(user, RoleCode.SUPER_ADMIN, assigned_by=None)
        self.audit.record(
            AuditAction.USER_REGISTERED,
            "user",
            user.id,
            user.id,
            {"bootstrap_super_admin": is_bootstrap_admin, "active": user.is_active},
        )
        await self.session.commit()
        await self.session.refresh(user, ["role_links"])
        return user

    async def ensure_bootstrap_roles(self, user: User) -> User:
        """Give SUPER_ADMIN to IDs listed in BOOTSTRAP_SUPER_ADMIN_IDS (first login after deploy)."""
        if user.messenger_user_id in self.bootstrap_ids and RoleCode.SUPER_ADMIN not in user.roles:
            await self._link_role(user, RoleCode.SUPER_ADMIN, assigned_by=None)
            user.is_active = True
            self.audit.record(
                AuditAction.ROLE_ASSIGNED, "user", user.id, None, {"role": "SUPER_ADMIN", "bootstrap": True}
            )
            await self.session.commit()
            await self.session.refresh(user, ["role_links"])
        return user

    async def touch_chat(self, user: User, chat_id: int, username: str | None) -> None:
        if user.chat_id != chat_id or user.username != username:
            user.chat_id = chat_id
            user.username = username
            await self.session.commit()

    async def list_page(self, actor: User, page: int, page_size: int) -> tuple[list[User], int]:
        access.require(actor, Permission.USERS_VIEW)
        return await self.users.list_page(page * page_size, page_size)

    async def set_role(self, actor: User, target_id: UUID, role: RoleCode, enabled: bool) -> User:
        access.require(actor, Permission.USERS_MANAGE_ROLES)
        target = await self.users.get(target_id, for_update=True)
        if target is None:
            raise NotFoundError("کاربر پیدا نشد.")
        if not enabled and role == RoleCode.SUPER_ADMIN and target.id == actor.id:
            raise ValidationError("نمی‌توانید نقش مدیر کل را از خودتان بگیرید.")
        if enabled and role not in target.roles:
            await self._link_role(target, role, assigned_by=actor.id)
            self.audit.record(AuditAction.ROLE_ASSIGNED, "user", target.id, actor.id, {"role": role.value})
        elif not enabled and role in target.roles:
            target.role_links = [link for link in target.role_links if link.role.code != role]
            self.audit.record(AuditAction.ROLE_REVOKED, "user", target.id, actor.id, {"role": role.value})
        await self.session.commit()
        await self.session.refresh(target, ["role_links"])
        return target

    async def set_active(self, actor: User, target_id: UUID, active: bool) -> User:
        access.require(actor, Permission.USERS_MANAGE_ROLES)
        target = await self.users.get(target_id, for_update=True)
        if target is None:
            raise NotFoundError("کاربر پیدا نشد.")
        if target.id == actor.id and not active:
            raise ValidationError("نمی‌توانید حساب خودتان را غیرفعال کنید.")
        if target.is_active != active:
            target.is_active = active
            action = AuditAction.USER_ACTIVATED if active else AuditAction.USER_DEACTIVATED
            self.audit.record(action, "user", target.id, actor.id, {})
            await self.session.commit()
        return target

    async def create_super_admin(self, messenger_user_id: int, full_name: str) -> User:
        """CLI bootstrap: create or promote a user to SUPER_ADMIN."""
        user = await self.users.get_by_messenger_id(messenger_user_id)
        if user is None:
            user = self.users.add(
                User(
                    messenger_user_id=messenger_user_id,
                    chat_id=messenger_user_id,
                    full_name=self.validate_full_name(full_name),
                    is_active=True,
                    registered_at=self.time.now_utc(),
                    role_links=[],
                )
            )
            await self.session.flush()
            await self._link_role(user, RoleCode.EMPLOYEE, assigned_by=None)
        user.is_active = True
        if RoleCode.SUPER_ADMIN not in user.roles:
            await self._link_role(user, RoleCode.SUPER_ADMIN, assigned_by=None)
        self.audit.record(AuditAction.ROLE_ASSIGNED, "user", user.id, None, {"role": "SUPER_ADMIN", "via": "cli"})
        await self.session.commit()
        return user

    async def _link_role(self, user: User, role: RoleCode, assigned_by: UUID | None) -> None:
        role_row = await self.roles.get_by_code(role)
        link = UserRole(user_id=user.id, role_id=role_row.id, assigned_by=assigned_by)
        link.role = role_row
        user.role_links.append(link)
        await self.session.flush()
