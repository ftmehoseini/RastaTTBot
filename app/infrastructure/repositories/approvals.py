from uuid import UUID

from sqlalchemy import select

from app.core.security.rbac import RoleCode
from app.domain.approvals.models import ApprovalStep, ApprovalStepStatus, WorkflowAction, WorkflowType
from app.infrastructure.repositories.base import Repository


class ApprovalRepository(Repository[ApprovalStep]):
    model = ApprovalStep

    async def steps_for(
        self, workflow_type: WorkflowType, entity_id: UUID, *, for_update: bool = False
    ) -> list[ApprovalStep]:
        stmt = (
            select(ApprovalStep)
            .where(ApprovalStep.workflow_type == workflow_type, ApprovalStep.entity_id == entity_id)
            .order_by(ApprovalStep.step_order)
        )
        if for_update:
            stmt = stmt.with_for_update()
        return list((await self.session.execute(stmt)).scalars())

    async def pending_entity_ids(self, workflow_type: WorkflowType, roles: set[RoleCode]) -> list[UUID]:
        stmt = (
            select(ApprovalStep.entity_id)
            .where(
                ApprovalStep.workflow_type == workflow_type,
                ApprovalStep.status == ApprovalStepStatus.PENDING,
                ApprovalStep.required_role.in_(roles),
            )
            .order_by(ApprovalStep.activated_at)
        )
        return list((await self.session.execute(stmt)).scalars())

    def add_action(self, action: WorkflowAction) -> WorkflowAction:
        self.session.add(action)
        return action

    async def history(self, workflow_type: WorkflowType, entity_id: UUID) -> list[WorkflowAction]:
        stmt = (
            select(WorkflowAction)
            .where(WorkflowAction.workflow_type == workflow_type, WorkflowAction.entity_id == entity_id)
            .order_by(WorkflowAction.created_at)
        )
        return list((await self.session.execute(stmt)).scalars())
