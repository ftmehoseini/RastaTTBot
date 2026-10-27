from dataclasses import asdict, dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services import access
from app.core.config.policy import WEEKDAY_CODES, WEEKDAY_ORDER, UsersPolicy
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.security.rbac import Permission, RoleCode
from app.core.utils.clock import TimeService
from app.core.utils.text import clean_text, normalize_employee_code
from app.domain.audit.models import AuditAction
from app.domain.users.models import SCHEDULE_HOURLY, EmployeeRosterEntry, User, UserRole
from app.infrastructure.repositories.audit import AuditRepository
from app.infrastructure.repositories.users import RoleRepository, RosterRepository, UserRepository


def _name_key(name: str) -> str:
    """Names compared loosely: Arabic ي/ك as Persian ی/ک, zero-width joiners and extra spaces ignored."""
    text = name.replace("ي", "ی").replace("ك", "ک").replace("\u200c", " ")
    return " ".join(text.split())


@dataclass
class RosterImport:
    added: int = 0
    updated: int = 0
    removed: int = 0
    kept_registered: int = 0  # not in the new file but already used to register, so kept
    linked: int = 0  # matched by name to people who registered before the list existed

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


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
        self.roster = RosterRepository(session)
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

    def is_valid_name(self, text: str) -> bool:
        try:
            self.validate_full_name(text)
        except ValidationError:
            return False
        return True

    def roster_required(self, messenger_user_id: int) -> bool:
        """Whether this person must give a number from the staff list to register."""
        return self.policy.require_employee_roster and messenger_user_id not in self.bootstrap_ids

    async def find_roster_entry(self, typed: str) -> EmployeeRosterEntry:
        """The free staff-list entry for a typed number, or a Persian error."""
        code = normalize_employee_code(typed)
        if code is None:
            raise ValidationError("شماره را فقط با رقم بنویسید، مثل ۰۹۱۲۱۲۳۴۵۶۷.")
        entry = await self.roster.get_by_code(code)
        if entry is None:
            raise NotFoundError("این شماره در فهرست کارکنان نیست. اگر درست نوشته‌اید، با مدیر سیستم تماس بگیرید.")
        if entry.user_id is not None:
            raise ConflictError("با این شماره قبلاً ثبت‌نام شده است. اگر اشتباه است، با مدیر سیستم تماس بگیرید.")
        return entry

    async def register(
        self,
        messenger_user_id: int,
        chat_id: int,
        full_name: str | None,
        username: str | None,
        roster_code: str | None = None,
    ) -> User:
        if await self.users.get_by_messenger_id(messenger_user_id):
            raise ConflictError("شما قبلاً ثبت‌نام کرده‌اید.")
        entry = None
        if roster_code is not None or self.roster_required(messenger_user_id):
            if roster_code is None:
                raise ValidationError("برای ثبت‌نام، شماره‌ی خود در فهرست کارکنان را وارد کنید.")
            entry = await self.find_roster_entry(roster_code)
            entry = await self.roster.get_by_code(entry.code, for_update=True)  # claim it exactly once
            if entry is None or entry.user_id is not None:
                raise ConflictError("با این شماره قبلاً ثبت‌نام شده است.")
        name = self.validate_full_name(full_name or (entry.full_name if entry else None) or "")
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
        if entry is not None:
            entry.user_id = user.id
            entry.claimed_at = self.time.now_utc()
            if entry.hourly:
                user.schedule_code = SCHEDULE_HOURLY
        await self._link_role(user, RoleCode.EMPLOYEE, assigned_by=None)
        if is_bootstrap_admin:
            await self._link_role(user, RoleCode.SUPER_ADMIN, assigned_by=None)
        self.audit.record(
            AuditAction.USER_REGISTERED,
            "user",
            user.id,
            user.id,
            {
                "bootstrap_super_admin": is_bootstrap_admin,
                "active": user.is_active,
                "roster_code": entry.code if entry else None,
            },
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

    async def set_hourly(self, actor: User, target_id: UUID, hourly: bool) -> User:
        """Switch a user between full-time (organization rules) and hourly (only worked time)."""
        access.require(actor, Permission.USERS_MANAGE_ROLES)
        target = await self.users.get(target_id, for_update=True)
        if target is None:
            raise NotFoundError("کاربر پیدا نشد.")
        code = SCHEDULE_HOURLY if hourly else None
        if target.schedule_code != code:
            self.audit.record(
                AuditAction.USER_SCHEDULE_CHANGED,
                "user",
                target.id,
                actor.id,
                {"from": target.schedule_code, "to": code},
            )
            target.schedule_code = code
            await self.session.commit()
        return target

    async def set_workdays(self, actor: User, target_id: UUID, codes: list[str] | None) -> User:
        """A person's own workdays (e.g. one who also works Thursday); None = the organization's."""
        access.require(actor, Permission.USERS_MANAGE_ROLES)
        target = await self.users.get(target_id, for_update=True)
        if target is None:
            raise NotFoundError("کاربر پیدا نشد.")
        value = None
        if codes is not None:
            unknown = [c for c in codes if c not in WEEKDAY_CODES]
            if unknown or not codes:
                raise ValidationError("دست‌کم یک روز کاری انتخاب کنید.")
            value = ",".join(c for c in WEEKDAY_ORDER if c in codes)
        if target.workdays != value:
            self.audit.record(
                AuditAction.USER_WORKDAYS_CHANGED, "user", target.id, actor.id, {"from": target.workdays, "to": value}
            )
            target.workdays = value
            await self.session.commit()
        return target

    # --- staff list ------------------------------------------------------------------------------

    async def roster_entries(self, actor: User) -> list[tuple[EmployeeRosterEntry, User | None]]:
        access.require(actor, Permission.USERS_MANAGE_ROLES)
        entries = await self.roster.list_all()
        users = await self.users.get_many({e.user_id for e in entries if e.user_id})
        return [(e, users.get(e.user_id) if e.user_id else None) for e in entries]

    async def roster_code_of(self, user: User) -> str | None:
        entry = await self.roster.get_by_user(user.id)
        return entry.code if entry else None

    async def import_roster(
        self, actor: User, rows: list[tuple[str, str | None, bool]], dry_run: bool = False
    ) -> RosterImport:
        """Replace the staff list: add and update the given numbers; drop the unused ones not in it.

        Numbers someone already registered with are never dropped. A changed type (hourly or
        full-time) is applied to that person's account too. With dry_run nothing changes: the
        result is the preview shown before the admin confirms.
        """
        access.require(actor, Permission.USERS_MANAGE_ROLES)
        existing = {e.code: e for e in await self.roster.list_all()}
        result = RosterImport()
        given = set()
        # People who registered before the list existed: link each to the entry with exactly their
        # name, so nobody else can register with their number.
        linked_ids = {e.user_id for e in existing.values() if e.user_id}
        by_name: dict[str, list[User]] = {}
        for u in await self.users.list_active():
            if u.id not in linked_ids:
                by_name.setdefault(_name_key(u.full_name), []).append(u)
        for code, name, hourly in rows:
            given.add(code)
            entry = existing.get(code)
            owner = None
            if name and (entry is None or entry.user_id is None):
                matches = by_name.get(_name_key(name), [])
                if len(matches) == 1 and matches[0].id not in linked_ids:
                    owner = matches[0]
                    linked_ids.add(owner.id)
                    result.linked += 1
            if entry is None:
                if not dry_run:
                    entry = self.roster.add(EmployeeRosterEntry(code=code, full_name=name, hourly=hourly))
                    if owner is not None:
                        entry.user_id, entry.claimed_at = owner.id, self.time.now_utc()
                        owner.schedule_code = SCHEDULE_HOURLY if hourly else None
                result.added += 1
                continue
            if (entry.full_name, entry.hourly) != (name, hourly):
                result.updated += 1
            if dry_run:
                continue
            entry.full_name, entry.hourly = name, hourly
            if owner is not None:
                entry.user_id, entry.claimed_at = owner.id, self.time.now_utc()
            if entry.user_id is not None:
                user = await self.users.get(entry.user_id)
                if user is not None and user.is_hourly != hourly:
                    user.schedule_code = SCHEDULE_HOURLY if hourly else None
        for code, entry in existing.items():
            if code in given:
                continue
            if entry.user_id is None:
                if not dry_run:
                    await self.session.delete(entry)
                result.removed += 1
            else:
                result.kept_registered += 1
        if dry_run:
            return result
        self.audit.record(AuditAction.ROSTER_IMPORTED, "employee_roster", None, actor.id, result.as_dict())
        await self.session.commit()
        return result

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
