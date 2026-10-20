from datetime import date
from uuid import UUID

from sqlalchemy import select

from app.domain.attendance.models import AttendanceDailySummary, AttendanceManualCorrection, AttendanceRecord
from app.infrastructure.repositories.base import Repository


class AttendanceRepository(Repository[AttendanceRecord]):
    model = AttendanceRecord

    async def get_for_day(self, user_id: UUID, work_date: date, *, for_update: bool = False) -> AttendanceRecord | None:
        stmt = select(AttendanceRecord).where(
            AttendanceRecord.user_id == user_id, AttendanceRecord.work_date == work_date
        )
        if for_update:
            stmt = stmt.with_for_update()
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_between(self, start: date, end: date, user_id: UUID | None = None) -> list[AttendanceRecord]:
        stmt = select(AttendanceRecord).where(AttendanceRecord.work_date >= start, AttendanceRecord.work_date <= end)
        if user_id is not None:
            stmt = stmt.where(AttendanceRecord.user_id == user_id)
        stmt = stmt.order_by(AttendanceRecord.work_date)
        return list((await self.session.execute(stmt)).scalars())

    async def user_ids_checked_in_on(self, work_date: date) -> set[UUID]:
        stmt = select(AttendanceRecord.user_id).where(
            AttendanceRecord.work_date == work_date, AttendanceRecord.check_in_at.is_not(None)
        )
        return set((await self.session.execute(stmt)).scalars())

    async def open_records_on(self, work_date: date) -> list[AttendanceRecord]:
        stmt = select(AttendanceRecord).where(
            AttendanceRecord.work_date == work_date,
            AttendanceRecord.check_in_at.is_not(None),
            AttendanceRecord.check_out_at.is_(None),
        )
        return list((await self.session.execute(stmt)).scalars())

    def add_correction(self, correction: AttendanceManualCorrection) -> AttendanceManualCorrection:
        self.session.add(correction)
        return correction

    async def corrections_between(
        self, start: date, end: date, user_id: UUID | None = None
    ) -> list[AttendanceManualCorrection]:
        stmt = select(AttendanceManualCorrection).where(
            AttendanceManualCorrection.work_date >= start, AttendanceManualCorrection.work_date <= end
        )
        if user_id is not None:
            stmt = stmt.where(AttendanceManualCorrection.user_id == user_id)
        stmt = stmt.order_by(AttendanceManualCorrection.submitted_at)
        return list((await self.session.execute(stmt)).scalars())

    async def get_summary(self, user_id: UUID, work_date: date) -> AttendanceDailySummary | None:
        stmt = select(AttendanceDailySummary).where(
            AttendanceDailySummary.user_id == user_id, AttendanceDailySummary.work_date == work_date
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    def add_summary(self, summary: AttendanceDailySummary) -> AttendanceDailySummary:
        self.session.add(summary)
        return summary
