import uuid
from datetime import date, datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import (
    AuthorshipMixin,
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    enum_column,
    utc_timestamp,
)


class RecordSource(StrEnum):
    BOT_LIVE = "BOT_LIVE"
    MANUAL_CORRECTION = "MANUAL_CORRECTION"
    ADMIN_EDIT = "ADMIN_EDIT"
    LEGACY_IMPORT = "LEGACY_IMPORT"


class CorrectionType(StrEnum):
    CHECK_IN = "CHECK_IN"
    CHECK_OUT = "CHECK_OUT"
    BOTH = "BOTH"


class CorrectionStatus(StrEnum):
    APPLIED = "APPLIED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    REJECTED = "REJECTED"


class DayStatus(StrEnum):
    COMPLETE = "COMPLETE"
    MISSING_CHECK_OUT = "MISSING_CHECK_OUT"
    MISSING_CHECK_IN = "MISSING_CHECK_IN"
    ABSENT = "ABSENT"
    NON_WORKDAY = "NON_WORKDAY"


class AttendanceRecord(UUIDPrimaryKeyMixin, TimestampMixin, AuthorshipMixin, Base):
    """One row per employee per local work date. Check-out may be after midnight (night shift)."""

    __tablename__ = "attendance_records"
    __table_args__ = (
        sa.UniqueConstraint("user_id", "work_date"),
        sa.CheckConstraint("check_out_at IS NULL OR check_in_at IS NULL OR check_out_at > check_in_at", name="order"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, sa.ForeignKey("users.id"), index=True)
    work_date: Mapped[date] = mapped_column(sa.Date, index=True)
    check_in_at: Mapped[datetime | None] = mapped_column(utc_timestamp())
    check_out_at: Mapped[datetime | None] = mapped_column(utc_timestamp())
    check_in_source: Mapped[RecordSource | None] = mapped_column(enum_column(RecordSource))
    check_out_source: Mapped[RecordSource | None] = mapped_column(enum_column(RecordSource))
    note: Mapped[str | None] = mapped_column(sa.String(300))


class AttendanceManualCorrection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Immutable trail of every forgotten-entry submission, including the values it replaced."""

    __tablename__ = "attendance_manual_corrections"

    record_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, sa.ForeignKey("attendance_records.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, sa.ForeignKey("users.id"), index=True)
    work_date: Mapped[date] = mapped_column(sa.Date)
    correction_type: Mapped[CorrectionType] = mapped_column(enum_column(CorrectionType))
    claimed_check_in_at: Mapped[datetime | None] = mapped_column(utc_timestamp())
    claimed_check_out_at: Mapped[datetime | None] = mapped_column(utc_timestamp())
    previous_check_in_at: Mapped[datetime | None] = mapped_column(utc_timestamp())
    previous_check_out_at: Mapped[datetime | None] = mapped_column(utc_timestamp())
    reason: Mapped[str | None] = mapped_column(sa.String(300))
    status: Mapped[CorrectionStatus] = mapped_column(enum_column(CorrectionStatus))
    submitted_by: Mapped[uuid.UUID] = mapped_column(sa.Uuid, sa.ForeignKey("users.id"))
    submitted_at: Mapped[datetime] = mapped_column(utc_timestamp())
    decided_by: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, sa.ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(utc_timestamp())
    # Set when a later correction replaces values from this one
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)


class AttendanceDailySummary(UUIDPrimaryKeyMixin, Base):
    """Calculated figures for one employee-day, refreshed after every change to the record."""

    __tablename__ = "attendance_daily_summaries"
    __table_args__ = (sa.UniqueConstraint("user_id", "work_date"),)

    user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, sa.ForeignKey("users.id"), index=True)
    work_date: Mapped[date] = mapped_column(sa.Date, index=True)
    record_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, sa.ForeignKey("attendance_records.id"))
    status: Mapped[DayStatus] = mapped_column(enum_column(DayStatus))
    gross_worked_minutes: Mapped[int | None] = mapped_column(sa.Integer)
    required_minutes: Mapped[int] = mapped_column(sa.Integer)
    late_minutes: Mapped[int] = mapped_column(sa.Integer, default=0)
    floating_late_minutes: Mapped[int] = mapped_column(sa.Integer, default=0, server_default="0")
    early_departure_minutes: Mapped[int] = mapped_column(sa.Integer, default=0)
    allowed_late_minutes: Mapped[int] = mapped_column(sa.Integer, default=0)
    allowed_early_minutes: Mapped[int] = mapped_column(sa.Integer, default=0)
    raw_balance_minutes: Mapped[int | None] = mapped_column(sa.Integer)
    overtime_minutes: Mapped[int] = mapped_column(sa.Integer, default=0)
    deficit_minutes: Mapped[int] = mapped_column(sa.Integer, default=0)
    net_balance_minutes: Mapped[int | None] = mapped_column(sa.Integer)
    policy_name: Mapped[str] = mapped_column(sa.String(40))
    calculated_at: Mapped[datetime] = mapped_column(utc_timestamp())
