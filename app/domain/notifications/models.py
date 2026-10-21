import uuid
from datetime import datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_column, utc_timestamp


class NotificationType(StrEnum):
    CHECK_IN_REMINDER = "CHECK_IN_REMINDER"
    CHECK_OUT_REMINDER = "CHECK_OUT_REMINDER"
    LEAVE_APPROVAL_REQUEST = "LEAVE_APPROVAL_REQUEST"
    LEAVE_STATUS_CHANGED = "LEAVE_STATUS_CHANGED"
    FOOD_MENU_BROADCAST = "FOOD_MENU_BROADCAST"
    GENERIC = "GENERIC"


class NotificationStatus(StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"


class NotificationLog(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One row per intended delivery.

    `dedup_key` is unique: inserting it is how jobs and bulk sends claim a recipient, so two
    workers (or a restarted job) can never send the same reminder twice.
    """

    __tablename__ = "notification_logs"

    user_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, sa.ForeignKey("users.id"), index=True)
    chat_id: Mapped[int] = mapped_column(sa.BigInteger)
    notification_type: Mapped[NotificationType] = mapped_column(enum_column(NotificationType), index=True)
    dedup_key: Mapped[str | None] = mapped_column(sa.String(160), unique=True)
    batch_key: Mapped[str | None] = mapped_column(sa.String(120), index=True)
    status: Mapped[NotificationStatus] = mapped_column(
        enum_column(NotificationStatus), default=NotificationStatus.PENDING
    )
    attempts: Mapped[int] = mapped_column(sa.Integer, default=0)
    error: Mapped[str | None] = mapped_column(sa.String(500))
    sent_at: Mapped[datetime | None] = mapped_column(utc_timestamp())
