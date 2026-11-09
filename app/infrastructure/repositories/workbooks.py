from uuid import UUID

from sqlalchemy import select

from app.domain.attendance.models import AttendanceWorkbook
from app.infrastructure.repositories.base import Repository


class WorkbookRepository(Repository[AttendanceWorkbook]):
    model = AttendanceWorkbook

    async def for_user(self, user_id: UUID, *, for_update: bool = False) -> AttendanceWorkbook | None:
        stmt = select(AttendanceWorkbook).where(AttendanceWorkbook.user_id == user_id)
        if for_update:
            stmt = stmt.with_for_update()
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_all(self) -> list[AttendanceWorkbook]:
        stmt = select(AttendanceWorkbook).order_by(AttendanceWorkbook.created_at.desc())
        return list((await self.session.execute(stmt)).scalars())
