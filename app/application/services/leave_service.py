"""Leave requests on top of the generic approval workflow."""

from datetime import date, time, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.dto.leave import LeaveDecisionResult, LeaveDraft, LeaveQuote, SubmitResult
from app.application.services import access
from app.application.services.approval_service import ApprovalService
from app.application.services.attendance_service import parse_holidays
from app.core.config.policy import Policy
from app.core.exceptions import ConflictError, NotFoundError, PermissionDeniedError, ValidationError
from app.core.security.rbac import Permission, RoleCode
from app.core.utils.clock import TimeService
from app.core.utils.jalali import iter_days
from app.core.utils.text import clean_text
from app.domain.approvals.engine import Decision, Outcome, StepDefinition, WorkflowDefinition
from app.domain.approvals.models import ApprovalStep, ApprovalStepStatus, WorkflowType
from app.domain.attendance.calculation import ScheduleResolver
from app.domain.audit.models import AuditAction
from app.domain.leave.models import (
    ACTIVE_LEAVE_STATUSES,
    OPEN_LEAVE_STATUSES,
    LeaveRequest,
    LeaveStatus,
    LeaveUnit,
)
from app.domain.users.models import User
from app.infrastructure.repositories.audit import AuditRepository
from app.infrastructure.repositories.leave import LeaveRepository
from app.infrastructure.repositories.users import UserRepository

STEP_STATUS = {1: LeaveStatus.PENDING_ADMIN_1, 2: LeaveStatus.PENDING_ADMIN_2}


def leave_workflow(policy: Policy) -> WorkflowDefinition:
    return WorkflowDefinition(
        workflow_type=WorkflowType.LEAVE_REQUEST,
        steps=(
            StepDefinition(1, RoleCode.ADMIN_1_SECRETARY, "بررسی منشی"),
            StepDefinition(2, RoleCode.ADMIN_2_MANAGER, "تأیید مدیر"),
        ),
        rejection_reason_required=policy.leave.require_rejection_reason,
        min_reason_length=policy.leave.min_rejection_reason_length,
    )


class LeaveService:
    def __init__(self, session: AsyncSession, policy: Policy, time: TimeService):
        self.session = session
        self.policy = policy.leave
        self.time = time
        self.workflow = leave_workflow(policy)
        self.approvals = ApprovalService(session, time)
        self.requests = LeaveRepository(session)
        self.users = UserRepository(session)
        self.audit = AuditRepository(session)
        self.schedules = ScheduleResolver(policy.attendance, parse_holidays(policy.organization.holidays))

    # --- drafting ------------------------------------------------------------------

    def date_window(self) -> tuple[date, date]:
        today = self.time.today()
        return today - timedelta(days=self.policy.max_past_days), today + timedelta(days=self.policy.max_future_days)

    def quote(self, draft: LeaveDraft) -> LeaveQuote:
        """Validate a draft and compute its interval and duration. Raises ValidationError."""
        earliest, latest = self.date_window()
        if draft.start_date < earliest:
            raise ValidationError(f"درخواست مرخصی فقط تا {self.policy.max_past_days} روز گذشته قابل ثبت است.")
        if draft.end_date > latest:
            raise ValidationError(f"درخواست مرخصی فقط تا {self.policy.max_future_days} روز آینده قابل ثبت است.")
        if draft.start_date > draft.end_date:
            raise ValidationError("تاریخ شروع نباید بعد از تاریخ پایان باشد.")

        if draft.unit == LeaveUnit.HOURLY:
            if draft.start_date != draft.end_date:
                raise ValidationError("مرخصی ساعتی باید در یک روز باشد.")
            if draft.start_time is None or draft.end_time is None:
                raise ValidationError("ساعت شروع و پایان مرخصی وارد نشده است.")
            if draft.end_time <= draft.start_time:
                raise ValidationError("ساعت پایان باید بعد از ساعت شروع باشد.")
            starts_at = self.time.local_to_utc(draft.start_date, draft.start_time)
            ends_at = self.time.local_to_utc(draft.start_date, draft.end_time)
            minutes = int((ends_at - starts_at).total_seconds() // 60)
            return LeaveQuote(draft, starts_at, ends_at, days_count=None, duration_minutes=minutes)

        days = iter_days(draft.start_date, draft.end_date)
        if not self.policy.count_non_workdays_in_daily_leave:
            days = [d for d in days if self.schedules.is_workday(d)]
        if not days:
            raise ValidationError("بازه‌ی انتخاب‌شده هیچ روز کاری ندارد.")
        if len(days) > self.policy.max_daily_leave_days:
            raise ValidationError(f"حداکثر طول مرخصی روزانه {self.policy.max_daily_leave_days} روز است.")
        starts_at = self.time.local_to_utc(draft.start_date, time.min)
        ends_at = self.time.local_to_utc(draft.end_date + timedelta(days=1), time.min)
        return LeaveQuote(draft, starts_at, ends_at, days_count=len(days), duration_minutes=None)

    async def _check_overlap(self, user: User, quote: LeaveQuote) -> None:
        if self.policy.allow_overlapping_leave_requests:
            return
        clashes = await self.requests.overlapping(user.id, quote.starts_at, quote.ends_at, ACTIVE_LEAVE_STATUSES)
        if clashes:
            raise ConflictError(f"این بازه با درخواست مرخصی شماره‌ی {clashes[0].display_number} هم‌پوشانی دارد.")

    async def preview(self, user: User, draft: LeaveDraft) -> LeaveQuote:
        access.require(user, Permission.LEAVE_SELF)
        quote = self.quote(draft)
        await self._check_overlap(user, quote)
        return quote

    # --- submission ----------------------------------------------------------------

    async def submit(self, user: User, draft: LeaveDraft) -> SubmitResult:
        access.require(user, Permission.LEAVE_SELF)
        # Serialize submissions per user so a double tap cannot create two overlapping requests.
        await self.users.get(user.id, for_update=True)
        quote = self.quote(draft)
        await self._check_overlap(user, quote)
        now = self.time.now_utc()
        request = self.requests.add(
            LeaveRequest(
                user_id=user.id,
                pay_type=draft.pay_type,
                unit=draft.unit,
                start_date=draft.start_date,
                end_date=draft.end_date,
                start_time=draft.start_time,
                end_time=draft.end_time,
                starts_at=quote.starts_at,
                ends_at=quote.ends_at,
                duration_minutes=quote.duration_minutes,
                days_count=quote.days_count,
                reason=clean_text(draft.reason, self.policy.max_reason_length) or None,
                status=STEP_STATUS[1],
                submitted_at=now,
                created_by=user.id,
            )
        )
        await self.session.flush()
        first_step = await self.approvals.start(self.workflow, request.id, user.id)
        self.audit.record(
            AuditAction.LEAVE_SUBMITTED,
            "leave_request",
            request.id,
            user.id,
            {"pay_type": draft.pay_type.value, "unit": draft.unit.value, "start": draft.start_date.isoformat()},
        )
        await self.session.commit()
        await self.session.refresh(request)
        approvers = await self.users.list_active_with_role(first_step.required_role)
        return SubmitResult(request, first_step, [a for a in approvers if a.id != user.id])

    # --- decisions -----------------------------------------------------------------

    async def get_request(self, request_id: UUID) -> LeaveRequest:
        request = await self.requests.get(request_id)
        if request is None:
            raise NotFoundError("درخواست مرخصی پیدا نشد.")
        return request

    async def decide(
        self, actor: User, request_id: UUID, step_order: int, decision: Decision, reason: str | None = None
    ) -> LeaveDecisionResult:
        permission = Permission.LEAVE_APPROVE_STEP_1 if step_order == 1 else Permission.LEAVE_APPROVE_STEP_2
        access.require(actor, permission)
        request = await self.requests.get(request_id, for_update=True)
        if request is None:
            raise NotFoundError("درخواست مرخصی پیدا نشد.")
        if request.user_id == actor.id:
            raise PermissionDeniedError("بررسی درخواست مرخصی خودتان مجاز نیست.")
        if request.status not in OPEN_LEAVE_STATUSES:
            raise ConflictError("این درخواست قبلاً بررسی شده است.")

        cleaned_reason = clean_text(reason, self.policy.max_reason_length) or None
        result = await self.approvals.decide(self.workflow, request.id, step_order, decision, actor, cleaned_reason)
        now = self.time.now_utc()
        if result.outcome == Outcome.REJECTED:
            request.status = LeaveStatus.REJECTED
            request.rejection_reason = cleaned_reason
            request.decided_at = now
            action = AuditAction.LEAVE_REJECTED
        elif result.outcome == Outcome.APPROVED:
            request.status = LeaveStatus.APPROVED
            request.decided_at = now
            action = AuditAction.LEAVE_APPROVED_STEP
        else:
            assert result.next_step is not None
            request.status = STEP_STATUS[result.next_step.step_order]
            action = AuditAction.LEAVE_APPROVED_STEP
        request.updated_by = actor.id
        self.audit.record(
            action,
            "leave_request",
            request.id,
            actor.id,
            {"step": step_order, "outcome": result.outcome.value, "reason": cleaned_reason},
        )
        await self.session.commit()

        employee = await self.users.get(request.user_id)
        assert employee is not None
        next_approvers: list[User] = []
        if result.next_step is not None:
            approvers = await self.users.list_active_with_role(result.next_step.required_role)
            next_approvers = [a for a in approvers if a.id != employee.id]
        return LeaveDecisionResult(
            request=request,
            employee=employee,
            approved=result.outcome != Outcome.REJECTED,
            final=result.outcome != Outcome.ADVANCED,
            next_approvers=next_approvers,
            next_step=result.next_step,
        )

    async def cancel(self, user: User, request_id: UUID) -> LeaveRequest:
        access.require(user, Permission.LEAVE_SELF)
        request = await self.requests.get(request_id, for_update=True)
        if request is None or request.user_id != user.id:
            raise NotFoundError("درخواست مرخصی پیدا نشد.")
        if request.status not in OPEN_LEAVE_STATUSES:
            raise ConflictError("فقط درخواست‌های در انتظار بررسی قابل لغو هستند.")
        await self.approvals.cancel(self.workflow, request.id, user)
        request.status = LeaveStatus.CANCELLED
        request.decided_at = self.time.now_utc()
        request.updated_by = user.id
        self.audit.record(AuditAction.LEAVE_CANCELLED, "leave_request", request.id, user.id, {})
        await self.session.commit()
        return request

    # --- queries -------------------------------------------------------------------

    async def my_requests(self, user: User, limit: int = 10) -> list[LeaveRequest]:
        access.require(user, Permission.LEAVE_SELF)
        return await self.requests.list_for_user(user.id, limit)

    async def current_step(self, request_id: UUID) -> ApprovalStep | None:
        steps = await self.approvals.steps(self.workflow, request_id)
        return next((s for s in steps if s.status == ApprovalStepStatus.PENDING), None)

    async def steps(self, request_id: UUID) -> list[ApprovalStep]:
        return await self.approvals.steps(self.workflow, request_id)

    async def pending_for(self, actor: User) -> list[LeaveRequest]:
        roles = set(actor.roles)
        if not (
            access.can(actor, Permission.LEAVE_APPROVE_STEP_1) or access.can(actor, Permission.LEAVE_APPROVE_STEP_2)
        ):
            raise PermissionDeniedError()
        ids = await self.approvals.pending_for_roles(self.workflow, roles)
        return [r for r in await self.requests.get_many(ids) if r.user_id != actor.id]

    async def list_for_report(
        self,
        actor: User,
        start: date | None,
        end: date | None,
        statuses: set[LeaveStatus] | None = None,
        user_id: UUID | None = None,
    ) -> list[LeaveRequest]:
        access.require(actor, Permission.LEAVE_REPORT_ALL)
        rows = await self.requests.list_filtered(start, end, statuses, user_id)
        self.audit.record(
            AuditAction.REPORT_GENERATED,
            "leave_report",
            None,
            actor.id,
            {"start": start.isoformat() if start else None, "end": end.isoformat() if end else None},
        )
        await self.session.commit()
        return rows

    async def users_by_id(self, ids: set[UUID]) -> dict[UUID, User]:
        return await self.users.get_many(ids)
