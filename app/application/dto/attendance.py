from dataclasses import dataclass, field
from datetime import date, datetime, time
from uuid import UUID

from app.domain.attendance.calculation import DailyCalculation
from app.domain.attendance.models import AttendanceRecord, CorrectionStatus, CorrectionType, RecordSource


@dataclass(frozen=True)
class PunchPreview:
    """What the user confirms before a live check-in/out is saved."""

    kind: str  # "in" | "out"
    at_utc: datetime
    at_local: datetime
    work_date: date
    existing: AttendanceRecord | None = None  # set when the punch is already registered


@dataclass(frozen=True)
class PunchResult:
    record: AttendanceRecord
    created: bool  # False: it already existed and nothing changed
    calculation: DailyCalculation | None = None


@dataclass(frozen=True)
class CorrectionInput:
    correction_type: CorrectionType
    work_date: date
    check_in_time: time | None = None
    check_out_time: time | None = None
    reason: str | None = None


@dataclass(frozen=True)
class CorrectionResult:
    correction_id: UUID
    status: CorrectionStatus
    record: AttendanceRecord | None
    check_in_local: datetime | None
    check_out_local: datetime | None


@dataclass(frozen=True)
class DayRow:
    user_id: UUID
    work_date: date
    check_in_local: datetime | None
    check_out_local: datetime | None
    check_in_source: RecordSource | None
    check_out_source: RecordSource | None
    calculation: DailyCalculation
    is_workday: bool
    correction_notes: list[str] = field(default_factory=list)

    @property
    def is_manual(self) -> bool:
        manual = {RecordSource.MANUAL_CORRECTION, RecordSource.ADMIN_EDIT}
        return self.check_in_source in manual or self.check_out_source in manual


@dataclass(frozen=True)
class Totals:
    worked_minutes: int = 0
    required_minutes: int = 0
    late_minutes: int = 0
    floating_late_minutes: int = 0
    early_departure_minutes: int = 0
    overtime_minutes: int = 0
    deficit_minutes: int = 0
    net_balance_minutes: int = 0
    present_days: int = 0
    absent_days: int = 0
    incomplete_days: int = 0

    @classmethod
    def of(cls, rows: list[DayRow]) -> "Totals":
        from app.domain.attendance.models import DayStatus

        c = [r.calculation for r in rows]
        return cls(
            worked_minutes=sum(x.gross_worked_minutes or 0 for x in c),
            required_minutes=sum(x.required_minutes for x in c),
            late_minutes=sum(x.late_minutes for x in c),
            floating_late_minutes=sum(x.floating_late_minutes for x in c),
            early_departure_minutes=sum(x.early_departure_minutes for x in c),
            overtime_minutes=sum(x.overtime_minutes for x in c),
            deficit_minutes=sum(x.deficit_minutes for x in c),
            net_balance_minutes=sum(x.net_balance_minutes or 0 for x in c),
            present_days=sum(x.status == DayStatus.COMPLETE for x in c),
            absent_days=sum(x.status == DayStatus.ABSENT for x in c),
            incomplete_days=sum(x.status in (DayStatus.MISSING_CHECK_OUT, DayStatus.MISSING_CHECK_IN) for x in c),
        )


@dataclass(frozen=True)
class EmployeeReport:
    user_id: UUID
    full_name: str
    messenger_user_id: int
    start: date
    end: date
    rows: list[DayRow]
    totals: Totals
    # Monthly caps, set only when the report covers one whole Jalali month (0 = no cap)
    late_cap_minutes: int = 0
    early_cap_minutes: int = 0
    hourly: bool = False


@dataclass(frozen=True)
class TeamSummary:
    start: date
    end: date
    employee_count: int
    present_count: int
    absent_count: int
    missing_checkout_count: int
    late_count: int
    manual_count: int
