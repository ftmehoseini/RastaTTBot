"""Pure approval state machine. No database or messenger code here.

A workflow is an ordered list of steps, each decided by holders of one role. Approving the
current step activates the next one; approving the last step approves the request.
Rejecting any step ends the chain. Persistence lives in `ApprovalService`.
"""

from dataclasses import dataclass
from enum import StrEnum

from app.core.exceptions import ConflictError, PermissionDeniedError, ValidationError
from app.core.security.rbac import RoleCode
from app.domain.approvals.models import ApprovalStepStatus, WorkflowType


@dataclass(frozen=True)
class StepDefinition:
    order: int
    role: RoleCode
    title_fa: str


@dataclass(frozen=True)
class WorkflowDefinition:
    workflow_type: WorkflowType
    steps: tuple[StepDefinition, ...]
    rejection_reason_required: bool = True
    min_reason_length: int = 3

    def step(self, order: int) -> StepDefinition:
        for step in self.steps:
            if step.order == order:
                return step
        raise KeyError(order)

    @property
    def last_order(self) -> int:
        return self.steps[-1].order


class Decision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class Outcome(StrEnum):
    ADVANCED = "ADVANCED"  # moved to the next step
    APPROVED = "APPROVED"  # final approval
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class StepState:
    order: int
    role: RoleCode
    status: ApprovalStepStatus


@dataclass(frozen=True)
class Transition:
    outcome: Outcome
    decided_step: int
    next_step: int | None
    # New status for every step order that changes
    step_statuses: dict[int, ApprovalStepStatus]


def initial_statuses(definition: WorkflowDefinition) -> dict[int, ApprovalStepStatus]:
    first = definition.steps[0].order
    return {
        s.order: ApprovalStepStatus.PENDING if s.order == first else ApprovalStepStatus.WAITING
        for s in definition.steps
    }


def current_step(states: list[StepState]) -> StepState | None:
    pending = [s for s in states if s.status == ApprovalStepStatus.PENDING]
    return pending[0] if pending else None


def decide(
    definition: WorkflowDefinition,
    states: list[StepState],
    step_order: int,
    decision: Decision,
    actor_roles: set[RoleCode],
    reason: str | None,
) -> Transition:
    """Validate a decision on `step_order` and compute the resulting step statuses.

    Raises ConflictError if the step is no longer the current one (duplicate or stale click).
    """
    current = current_step(states)
    if current is None or current.order != step_order:
        raise ConflictError("این مرحله قبلاً بررسی شده یا دیگر فعال نیست.")
    if current.role not in actor_roles and RoleCode.SUPER_ADMIN not in actor_roles:
        raise PermissionDeniedError()

    if decision == Decision.REJECT:
        cleaned = (reason or "").strip()
        if definition.rejection_reason_required and len(cleaned) < definition.min_reason_length:
            raise ValidationError("نوشتن دلیل رد درخواست الزامی است.")
        statuses = {step_order: ApprovalStepStatus.REJECTED}
        for s in states:
            if s.status == ApprovalStepStatus.WAITING:
                statuses[s.order] = ApprovalStepStatus.CANCELLED
        return Transition(Outcome.REJECTED, step_order, None, statuses)

    statuses = {step_order: ApprovalStepStatus.APPROVED}
    later = sorted((s for s in states if s.order > step_order), key=lambda s: s.order)
    if later:
        statuses[later[0].order] = ApprovalStepStatus.PENDING
        return Transition(Outcome.ADVANCED, step_order, later[0].order, statuses)
    return Transition(Outcome.APPROVED, step_order, None, statuses)


def cancel(states: list[StepState]) -> dict[int, ApprovalStepStatus]:
    """Withdraw a request: every open step is closed."""
    open_statuses = {ApprovalStepStatus.PENDING, ApprovalStepStatus.WAITING}
    if not any(s.status in open_statuses for s in states):
        raise ConflictError("این درخواست دیگر قابل لغو نیست.")
    return {s.order: ApprovalStepStatus.CANCELLED for s in states if s.status in open_statuses}
