from datetime import date, datetime
from uuid import UUID

from sqlalchemy import select

from app.domain.leave.models import LeaveRequest, LeaveStatus
from app.infrastructure.repositories.base import Repository


class LeaveRepository(Repository[LeaveRequest]):
    model = LeaveRequest

    async def overlapping(
        self, user_id: UUID, starts_at: datetime, ends_at: datetime, statuses: frozenset[LeaveStatus]
    ) -> list[LeaveRequest]:
        stmt = select(LeaveRequest).where(
            LeaveRequest.user_id == user_id,
            LeaveRequest.status.in_(statuses),
            LeaveRequest.starts_at < ends_at,
            LeaveRequest.ends_at > starts_at,
        )
        return list((await self.session.execute(stmt)).scalars())

    async def list_for_user(self, user_id: UUID, limit: int = 10) -> list[LeaveRequest]:
        stmt = (
            select(LeaveRequest)
            .where(LeaveRequest.user_id == user_id)
            .order_by(LeaveRequest.submitted_at.desc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def list_filtered(
        self,
        start: date | None = None,
        end: date | None = None,
        statuses: set[LeaveStatus] | None = None,
        user_id: UUID | None = None,
    ) -> list[LeaveRequest]:
        stmt = select(LeaveRequest)
        if start is not None:
            stmt = stmt.where(LeaveRequest.end_date >= start)
        if end is not None:
            stmt = stmt.where(LeaveRequest.start_date <= end)
        if statuses:
            stmt = stmt.where(LeaveRequest.status.in_(statuses))
        if user_id is not None:
            stmt = stmt.where(LeaveRequest.user_id == user_id)
        stmt = stmt.order_by(LeaveRequest.start_date, LeaveRequest.submitted_at)
        return list((await self.session.execute(stmt)).scalars())

    async def get_many(self, ids: list[UUID]) -> list[LeaveRequest]:
        if not ids:
            return []
        stmt = select(LeaveRequest).where(LeaveRequest.id.in_(ids)).order_by(LeaveRequest.submitted_at)
        return list((await self.session.execute(stmt)).scalars())
