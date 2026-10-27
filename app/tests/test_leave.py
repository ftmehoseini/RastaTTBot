from datetime import date, time

import pytest
from sqlalchemy import select

from app.application.dto.leave import LeaveDraft
from app.core.exceptions import ConflictError, PermissionDeniedError, ValidationError
from app.core.security.rbac import RoleCode
from app.domain.approvals.engine import (
    Decision,
    Outcome,
    StepDefinition,
    StepState,
    WorkflowDefinition,
    cancel,
    decide,
    initial_statuses,
)
from app.domain.approvals.models import ApprovalStepStatus, WorkflowAction, WorkflowType
from app.domain.leave.models import LeavePayType, LeaveStatus, LeaveUnit
from app.tests.conftest import tehran

DAILY = LeaveDraft(LeavePayType.PAID, LeaveUnit.DAILY, date(2026, 10, 21), date(2026, 10, 22), reason="سفر")
HOURLY = LeaveDraft(
    LeavePayType.UNPAID, LeaveUnit.HOURLY, date(2026, 10, 20), date(2026, 10, 20), time(10, 0), time(12, 30)
)


@pytest.fixture
async def people(make_user):
    return {
        "employee": await make_user("کارمند"),
        "secretary": await make_user("منشی", RoleCode.ADMIN_1_SECRETARY),
        "manager": await make_user("مدیر", RoleCode.ADMIN_2_MANAGER),
    }


# --- pure engine ---------------------------------------------------------------------------------

DEFINITION = WorkflowDefinition(
    WorkflowType.LEAVE_REQUEST,
    (StepDefinition(1, RoleCode.ADMIN_1_SECRETARY, "a"), StepDefinition(2, RoleCode.ADMIN_2_MANAGER, "b")),
)


def states(s1: ApprovalStepStatus, s2: ApprovalStepStatus) -> list[StepState]:
    return [StepState(1, RoleCode.ADMIN_1_SECRETARY, s1), StepState(2, RoleCode.ADMIN_2_MANAGER, s2)]


def test_engine_transitions() -> None:
    start = initial_statuses(DEFINITION)
    assert start == {1: ApprovalStepStatus.PENDING, 2: ApprovalStepStatus.WAITING}
    t = decide(
        DEFINITION,
        states(ApprovalStepStatus.PENDING, ApprovalStepStatus.WAITING),
        1,
        Decision.APPROVE,
        {RoleCode.ADMIN_1_SECRETARY},
        None,
    )
    assert t.outcome == Outcome.ADVANCED and t.next_step == 2
    t = decide(
        DEFINITION,
        states(ApprovalStepStatus.APPROVED, ApprovalStepStatus.PENDING),
        2,
        Decision.APPROVE,
        {RoleCode.ADMIN_2_MANAGER},
        None,
    )
    assert t.outcome == Outcome.APPROVED
    t = decide(
        DEFINITION,
        states(ApprovalStepStatus.PENDING, ApprovalStepStatus.WAITING),
        1,
        Decision.REJECT,
        {RoleCode.ADMIN_1_SECRETARY},
        "دلیل کافی",
    )
    assert t.outcome == Outcome.REJECTED and t.step_statuses[2] == ApprovalStepStatus.CANCELLED


def test_engine_guards() -> None:
    pending = states(ApprovalStepStatus.PENDING, ApprovalStepStatus.WAITING)
    with pytest.raises(PermissionDeniedError):  # manager cannot decide the secretary's step
        decide(DEFINITION, pending, 1, Decision.APPROVE, {RoleCode.ADMIN_2_MANAGER}, None)
    with pytest.raises(ConflictError):  # step 2 is not active yet
        decide(DEFINITION, pending, 2, Decision.APPROVE, {RoleCode.ADMIN_2_MANAGER}, None)
    with pytest.raises(ValidationError):  # reason required
        decide(DEFINITION, pending, 1, Decision.REJECT, {RoleCode.ADMIN_1_SECRETARY}, "  ")
    done = states(ApprovalStepStatus.APPROVED, ApprovalStepStatus.APPROVED)
    with pytest.raises(ConflictError):
        cancel(done)


# --- service --------------------------------------------------------------------------------------


async def test_full_two_step_approval(services, people, messenger) -> None:
    employee, secretary, manager = people["employee"], people["secretary"], people["manager"]
    submitted = await services.leave.submit(employee, DAILY)
    request = submitted.request
    assert request.status == LeaveStatus.PENDING_ADMIN_1
    assert request.days_count == 2
    # Only the secretary is asked first.
    assert [a.id for a in submitted.approvers] == [secretary.id]

    with pytest.raises(PermissionDeniedError):
        await services.leave.decide(manager, request.id, 1, Decision.APPROVE)

    step1 = await services.leave.decide(secretary, request.id, 1, Decision.APPROVE)
    assert step1.request.status == LeaveStatus.PENDING_ADMIN_2
    assert not step1.final and [a.id for a in step1.next_approvers] == [manager.id]

    # A second click on the old button (or another secretary) is rejected.
    with pytest.raises(ConflictError):
        await services.leave.decide(secretary, request.id, 1, Decision.APPROVE)

    step2 = await services.leave.decide(manager, request.id, 2, Decision.APPROVE)
    assert step2.final and step2.approved
    assert step2.request.status == LeaveStatus.APPROVED
    assert step2.request.decided_at is not None

    actions = (await services.session.execute(select(WorkflowAction))).scalars().all()
    assert {a.action.value for a in actions} >= {"SUBMIT", "APPROVE"}


async def test_rejection_requires_reason_and_ends_chain(services, people) -> None:
    employee, secretary, manager = people["employee"], people["secretary"], people["manager"]
    request = (await services.leave.submit(employee, HOURLY)).request
    assert request.duration_minutes == 150
    with pytest.raises(ValidationError):
        await services.leave.decide(secretary, request.id, 1, Decision.REJECT, "")
    result = await services.leave.decide(secretary, request.id, 1, Decision.REJECT, "روز شلوغی است")
    assert result.request.status == LeaveStatus.REJECTED
    assert result.request.rejection_reason == "روز شلوغی است"
    assert result.final and not result.approved and result.next_step is None
    with pytest.raises(ConflictError):
        await services.leave.decide(manager, request.id, 2, Decision.APPROVE)


async def test_manager_rejection_at_step_two(services, people) -> None:
    request = (await services.leave.submit(people["employee"], DAILY)).request
    await services.leave.decide(people["secretary"], request.id, 1, Decision.APPROVE)
    result = await services.leave.decide(people["manager"], request.id, 2, Decision.REJECT, "نیروی کافی نداریم")
    assert result.request.status == LeaveStatus.REJECTED


async def test_overlap_and_validation(services, people, clock) -> None:
    employee = people["employee"]
    await services.leave.submit(employee, DAILY)
    overlapping = LeaveDraft(
        LeavePayType.UNPAID, LeaveUnit.HOURLY, date(2026, 10, 22), date(2026, 10, 22), time(9, 0), time(10, 0)
    )
    with pytest.raises(ConflictError):
        await services.leave.submit(employee, overlapping)

    bad_drafts = [
        LeaveDraft(LeavePayType.PAID, LeaveUnit.DAILY, date(2026, 10, 25), date(2026, 10, 24)),  # start after end
        LeaveDraft(
            LeavePayType.PAID, LeaveUnit.HOURLY, date(2026, 10, 26), date(2026, 10, 26), time(12, 0), time(11, 0)
        ),
        LeaveDraft(
            LeavePayType.PAID, LeaveUnit.HOURLY, date(2026, 10, 26), date(2026, 10, 27), time(9, 0), time(11, 0)
        ),
        LeaveDraft(LeavePayType.PAID, LeaveUnit.DAILY, date(2026, 9, 1), date(2026, 9, 1)),  # too far in the past
        LeaveDraft(LeavePayType.PAID, LeaveUnit.DAILY, date(2027, 6, 1), date(2027, 6, 1)),  # too far ahead
    ]
    for draft in bad_drafts:
        with pytest.raises(ValidationError):
            await services.leave.submit(employee, draft)


async def test_overlap_allowed_by_policy(services, people, policy) -> None:
    policy.leave.allow_overlapping_leave_requests = True
    await services.leave.submit(people["employee"], DAILY)
    second = await services.leave.submit(people["employee"], DAILY)
    assert second.request.status == LeaveStatus.PENDING_ADMIN_1


async def test_self_approval_denied_and_cancel(services, people, make_user) -> None:
    secretary = people["secretary"]
    own = (await services.leave.submit(secretary, DAILY)).request
    with pytest.raises(PermissionDeniedError):
        await services.leave.decide(secretary, own.id, 1, Decision.APPROVE)
    cancelled = await services.leave.cancel(secretary, own.id)
    assert cancelled.status == LeaveStatus.CANCELLED
    with pytest.raises(ConflictError):
        await services.leave.cancel(secretary, own.id)
    # A cancelled request no longer blocks the same dates.
    again = await services.leave.submit(secretary, DAILY)
    assert again.request.status == LeaveStatus.PENDING_ADMIN_1


async def test_pending_inbox_by_role(services, people) -> None:
    request = (await services.leave.submit(people["employee"], DAILY)).request
    assert [r.id for r in await services.leave.pending_for(people["secretary"])] == [request.id]
    assert await services.leave.pending_for(people["manager"]) == []
    await services.leave.decide(people["secretary"], request.id, 1, Decision.APPROVE)
    assert [r.id for r in await services.leave.pending_for(people["manager"])] == [request.id]
    with pytest.raises(PermissionDeniedError):
        await services.leave.pending_for(people["employee"])


async def test_daily_leave_can_exclude_non_workdays(services, people, policy, clock) -> None:
    policy.leave.count_non_workdays_in_daily_leave = False
    clock.set(tehran(2026, 10, 19, 8, 0))
    draft = LeaveDraft(LeavePayType.PAID, LeaveUnit.DAILY, date(2026, 10, 22), date(2026, 10, 24))  # Thu–Sat
    quote = await services.leave.preview(people["employee"], draft)
    assert quote.days_count == 1  # Thursday and Friday are not workdays
