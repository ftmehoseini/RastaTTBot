"""Persists the generic approval workflow. Any request type can use it via a WorkflowDefinition."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError
from app.core.security.rbac import RoleCode
from app.core.utils.clock import TimeService
from app.domain.approvals import engine
from app.domain.approvals.engine import Decision, Outcome, StepState, WorkflowDefinition
from app.domain.approvals.models import ApprovalStep, ApprovalStepStatus, WorkflowAction, WorkflowActionType
from app.domain.users.models import User
from app.infrastructure.repositories.approvals import ApprovalRepository


@dataclass(frozen=True)
class DecisionResult:
    outcome: Outcome
    decided_step: ApprovalStep
    next_step: ApprovalStep | None


class ApprovalService:
    def __init__(self, session: AsyncSession, time: TimeService):
        self.session = session
        self.time = time
        self.repo = ApprovalRepository(session)

    async def start(self, definition: WorkflowDefinition, entity_id: UUID, submitted_by: UUID) -> ApprovalStep:
        """Create all steps for a new request. Returns the first (pending) step. Does not commit."""
        now = self.time.now_utc()
        statuses = engine.initial_statuses(definition)
        first: ApprovalStep | None = None
        for step_def in definition.steps:
            step = ApprovalStep(
                workflow_type=definition.workflow_type,
                entity_id=entity_id,
                step_order=step_def.order,
                required_role=step_def.role,
                status=statuses[step_def.order],
                activated_at=now if statuses[step_def.order] == ApprovalStepStatus.PENDING else None,
            )
            self.repo.add(step)
            if first is None:
                first = step
        self.repo.add_action(
            WorkflowAction(
                workflow_type=definition.workflow_type,
                entity_id=entity_id,
                step_order=None,
                action=WorkflowActionType.SUBMIT,
                actor_user_id=submitted_by,
                from_status=None,
                to_status=ApprovalStepStatus.PENDING.value,
            )
        )
        await self.session.flush()
        assert first is not None
        return first

    async def steps(self, definition: WorkflowDefinition, entity_id: UUID) -> list[ApprovalStep]:
        return await self.repo.steps_for(definition.workflow_type, entity_id)

    async def decide(
        self,
        definition: WorkflowDefinition,
        entity_id: UUID,
        step_order: int,
        decision: Decision,
        actor: User,
        reason: str | None = None,
    ) -> DecisionResult:
        """Apply a decision with the steps row-locked, so two admins clicking at once cannot both win.

        Does not commit: the caller updates its own entity in the same transaction and commits.
        """
        steps = await self.repo.steps_for(definition.workflow_type, entity_id, for_update=True)
        if not steps:
            raise ConflictError("این درخواست پیدا نشد.")
        states = [StepState(s.step_order, s.required_role, s.status) for s in steps]
        transition = engine.decide(definition, states, step_order, decision, actor.roles, reason)

        now = self.time.now_utc()
        by_order = {s.step_order: s for s in steps}
        for order, status in transition.step_statuses.items():
            step = by_order[order]
            previous = step.status
            step.status = status
            if order == step_order:
                step.decided_by = actor.id
                step.decided_at = now
                step.comment = (reason or "").strip() or None
            if status == ApprovalStepStatus.PENDING:
                step.activated_at = now
            self.repo.add_action(
                WorkflowAction(
                    workflow_type=definition.workflow_type,
                    entity_id=entity_id,
                    step_order=order,
                    # Side effects on other steps (activation, cancellation) are logged under
                    # the decision that caused them.
                    action=WorkflowActionType.APPROVE if decision == Decision.APPROVE else WorkflowActionType.REJECT,
                    actor_user_id=actor.id,
                    from_status=previous.value,
                    to_status=status.value,
                    comment=step.comment if order == step_order else None,
                )
            )
        await self.session.flush()
        next_step = by_order[transition.next_step] if transition.next_step is not None else None
        return DecisionResult(transition.outcome, by_order[step_order], next_step)

    async def cancel(self, definition: WorkflowDefinition, entity_id: UUID, actor: User) -> None:
        steps = await self.repo.steps_for(definition.workflow_type, entity_id, for_update=True)
        states = [StepState(s.step_order, s.required_role, s.status) for s in steps]
        changes = engine.cancel(states)
        for step in steps:
            if step.step_order in changes:
                previous = step.status
                step.status = changes[step.step_order]
                self.repo.add_action(
                    WorkflowAction(
                        workflow_type=definition.workflow_type,
                        entity_id=entity_id,
                        step_order=step.step_order,
                        action=WorkflowActionType.CANCEL,
                        actor_user_id=actor.id,
                        from_status=previous.value,
                        to_status=step.status.value,
                    )
                )
        await self.session.flush()

    async def pending_for_roles(self, definition: WorkflowDefinition, roles: set[RoleCode]) -> list[UUID]:
        if RoleCode.SUPER_ADMIN in roles:
            roles = {s.role for s in definition.steps}
        return await self.repo.pending_entity_ids(definition.workflow_type, roles)
