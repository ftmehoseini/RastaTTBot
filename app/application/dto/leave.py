from dataclasses import dataclass
from datetime import date, datetime, time

from app.domain.approvals.models import ApprovalStep
from app.domain.leave.models import LeavePayType, LeaveRequest, LeaveUnit
from app.domain.users.models import User


@dataclass(frozen=True)
class LeaveDraft:
    pay_type: LeavePayType
    unit: LeaveUnit
    start_date: date
    end_date: date
    start_time: time | None = None
    end_time: time | None = None
    reason: str | None = None


@dataclass(frozen=True)
class LeaveQuote:
    """The validated, computed form of a draft, shown in the review step."""

    draft: LeaveDraft
    starts_at: datetime
    ends_at: datetime
    days_count: int | None
    duration_minutes: int | None


@dataclass(frozen=True)
class SubmitResult:
    request: LeaveRequest
    first_step: ApprovalStep
    approvers: list[User]


@dataclass(frozen=True)
class LeaveDecisionResult:
    request: LeaveRequest
    employee: User
    approved: bool
    final: bool
    next_approvers: list[User]
    next_step: ApprovalStep | None
