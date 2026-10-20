"""Generic approval workflow storage, shared by every request type (leave, corrections, ...)."""

import uuid
from datetime import datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, UUIDPrimaryKeyMixin, enum_column, utc_timestamp
from app.core.security.rbac import RoleCode


class WorkflowType(StrEnum):
    LEAVE_REQUEST = "LEAVE_REQUEST"
    ATTENDANCE_CORRECTION = "ATTENDANCE_CORRECTION"


class ApprovalStepStatus(StrEnum):
    WAITING = "WAITING"  # a later step; not visible to its approvers yet
    PENDING = "PENDING"  # the current step
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"  # closed because the chain ended (rejection or withdrawal)


class WorkflowActionType(StrEnum):
    SUBMIT = "SUBMIT"
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    CANCEL = "CANCEL"


class ApprovalStep(UUIDPrimaryKeyMixin, Base):
    """One approval step of one request. This is the `leave_approval_steps` table, generalized."""

    __tablename__ = "approval_steps"
    __table_args__ = (
        sa.UniqueConstraint("workflow_type", "entity_id", "step_order"),
        sa.Index("ix_approval_steps_pending", "status", "required_role"),
    )

    workflow_type: Mapped[WorkflowType] = mapped_column(enum_column(WorkflowType))
    entity_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, index=True)
    step_order: Mapped[int] = mapped_column(sa.SmallInteger)
    required_role: Mapped[RoleCode] = mapped_column(enum_column(RoleCode))
    status: Mapped[ApprovalStepStatus] = mapped_column(enum_column(ApprovalStepStatus))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, sa.ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(utc_timestamp())
    comment: Mapped[str | None] = mapped_column(sa.String(500))
    activated_at: Mapped[datetime | None] = mapped_column(utc_timestamp())
    created_at: Mapped[datetime] = mapped_column(utc_timestamp(), server_default=sa.func.now())


class WorkflowAction(UUIDPrimaryKeyMixin, Base):
    """Append-only history of every workflow transition."""

    __tablename__ = "workflow_actions"
    __table_args__ = (sa.Index("ix_workflow_actions_entity", "workflow_type", "entity_id"),)

    workflow_type: Mapped[WorkflowType] = mapped_column(enum_column(WorkflowType))
    entity_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid)
    step_order: Mapped[int | None] = mapped_column(sa.SmallInteger)
    action: Mapped[WorkflowActionType] = mapped_column(enum_column(WorkflowActionType))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, sa.ForeignKey("users.id"))
    from_status: Mapped[str | None] = mapped_column(sa.String(40))
    to_status: Mapped[str] = mapped_column(sa.String(40))
    comment: Mapped[str | None] = mapped_column(sa.String(500))
    created_at: Mapped[datetime] = mapped_column(utc_timestamp(), server_default=sa.func.now())
