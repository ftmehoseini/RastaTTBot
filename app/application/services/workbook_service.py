"""Admin-uploaded attendance workbooks: upload, assign to a person, keep every version.

The Excel formulas belong to the admin; this service never looks at them. See
infrastructure/excel/workbook.py for how cells are found, and use_cases/workbook_report.py for the
write → recalculate → report flow.
"""

import asyncio
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services import access
from app.core.exceptions import NotFoundError, ValidationError
from app.core.security.rbac import Permission
from app.core.utils.text import clean_text
from app.domain.attendance.models import AttendanceWorkbook
from app.domain.audit.models import AuditAction
from app.domain.users.models import User
from app.infrastructure.excel.workbook import WorkbookError, WorkbookStore, inspect_workbook
from app.infrastructure.repositories.audit import AuditRepository
from app.infrastructure.repositories.users import UserRepository
from app.infrastructure.repositories.workbooks import WorkbookRepository


class WorkbookService:
    def __init__(self, session: AsyncSession, store: WorkbookStore):
        self.session = session
        self.store = store
        self.repo = WorkbookRepository(session)
        self.users = UserRepository(session)
        self.audit = AuditRepository(session)

    async def upload(self, actor: User, filename: str, content: bytes) -> AttendanceWorkbook:
        access.require(actor, Permission.USERS_MANAGE_ROLES)
        try:
            info = await asyncio.to_thread(inspect_workbook, content)
        except WorkbookError as exc:
            raise ValidationError(str(exc)) from exc
        title = clean_text(filename, 200) or "attendance.xlsx"
        workbook = self.repo.add(
            AttendanceWorkbook(title=title, sheets="،".join(info.sheets)[:300], version=1, uploaded_by=actor.id)
        )
        await self.session.flush()
        await asyncio.to_thread(self.store.save, workbook.id, 1, content)
        self.audit.record(AuditAction.WORKBOOK_UPLOADED, "attendance_workbook", workbook.id, actor.id, {"title": title})
        await self.session.commit()
        return workbook

    async def list_all(self, actor: User) -> list[tuple[AttendanceWorkbook, User | None]]:
        access.require(actor, Permission.USERS_MANAGE_ROLES)
        workbooks = await self.repo.list_all()
        owners = await self.users.get_many({w.user_id for w in workbooks if w.user_id})
        return [(w, owners.get(w.user_id) if w.user_id else None) for w in workbooks]

    async def get(self, actor: User, workbook_id: UUID) -> AttendanceWorkbook:
        access.require(actor, Permission.USERS_MANAGE_ROLES)
        workbook = await self.repo.get(workbook_id)
        if workbook is None:
            raise NotFoundError("فایل اکسل پیدا نشد.")
        return workbook

    async def for_user(self, user_id: UUID) -> AttendanceWorkbook | None:
        """The person's own workbook, if an admin assigned one (no permission check: internal use)."""
        return await self.repo.for_user(user_id)

    async def assign(self, actor: User, workbook_id: UUID | None, user_id: UUID) -> AttendanceWorkbook | None:
        """Give a person this workbook (None = no workbook). A person has at most one."""
        access.require(actor, Permission.USERS_MANAGE_ROLES)
        target = await self.users.get(user_id)
        if target is None:
            raise NotFoundError("کاربر پیدا نشد.")
        current = await self.repo.for_user(user_id, for_update=True)
        chosen = None
        if workbook_id is not None:
            chosen = await self.repo.get(workbook_id, for_update=True)
            if chosen is None:
                raise NotFoundError("فایل اکسل پیدا نشد.")
        if current is not None and current is not chosen:
            current.user_id = None
            await self.session.flush()  # the unique user_id must be free before it is reused
        if chosen is not None:
            chosen.user_id = user_id
        self.audit.record(
            AuditAction.WORKBOOK_ASSIGNED,
            "user",
            user_id,
            actor.id,
            {"workbook_id": str(chosen.id) if chosen else None, "previous": str(current.id) if current else None},
        )
        await self.session.commit()
        return chosen

    async def content(self, actor: User, workbook: AttendanceWorkbook) -> bytes:
        """The current version, as the admin would open it in Excel."""
        access.require(actor, Permission.USERS_MANAGE_ROLES)
        return await asyncio.to_thread(self.store.read, workbook.id, workbook.version)
