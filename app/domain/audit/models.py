import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, JSONType, UUIDPrimaryKeyMixin, enum_column, utc_timestamp


class AuditAction(StrEnum):
    USER_REGISTERED = "USER_REGISTERED"
    USER_ACTIVATED = "USER_ACTIVATED"
    USER_DEACTIVATED = "USER_DEACTIVATED"
    USER_SCHEDULE_CHANGED = "USER_SCHEDULE_CHANGED"
    USER_WORKDAYS_CHANGED = "USER_WORKDAYS_CHANGED"
    ROSTER_IMPORTED = "ROSTER_IMPORTED"
    ROLE_ASSIGNED = "ROLE_ASSIGNED"
    ROLE_REVOKED = "ROLE_REVOKED"
    ATTENDANCE_CHECK_IN = "ATTENDANCE_CHECK_IN"
    ATTENDANCE_CHECK_OUT = "ATTENDANCE_CHECK_OUT"
    ATTENDANCE_MANUAL_CORRECTION = "ATTENDANCE_MANUAL_CORRECTION"
    ATTENDANCE_LEGACY_IMPORT = "ATTENDANCE_LEGACY_IMPORT"
    LEAVE_SUBMITTED = "LEAVE_SUBMITTED"
    LEAVE_APPROVED_STEP = "LEAVE_APPROVED_STEP"
    LEAVE_REJECTED = "LEAVE_REJECTED"
    LEAVE_CANCELLED = "LEAVE_CANCELLED"
    FOOD_MENU_IMPORTED = "FOOD_MENU_IMPORTED"
    FOOD_ORDER_PLACED = "FOOD_ORDER_PLACED"
    FOOD_ORDER_CANCELLED = "FOOD_ORDER_CANCELLED"
    FOOD_GUEST_ORDER_PLACED = "FOOD_GUEST_ORDER_PLACED"
    FOOD_GUEST_ORDER_CANCELLED = "FOOD_GUEST_ORDER_CANCELLED"
    REPORT_GENERATED = "REPORT_GENERATED"
    BULK_NOTIFICATION = "BULK_NOTIFICATION"


class AuditLog(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (sa.Index("ix_audit_logs_entity", "entity_type", "entity_id"),)

    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, sa.ForeignKey("users.id"), index=True)
    action: Mapped[AuditAction] = mapped_column(enum_column(AuditAction), index=True)
    entity_type: Mapped[str] = mapped_column(sa.String(60))
    entity_id: Mapped[str | None] = mapped_column(sa.String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    correlation_id: Mapped[str | None] = mapped_column(sa.String(32))
    created_at: Mapped[datetime] = mapped_column(utc_timestamp(), server_default=sa.func.now(), index=True)
