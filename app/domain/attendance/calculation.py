"""Work-duration, lateness, early-departure and overtime calculation.

The organization picks a strategy by name (`attendance.calculation_policy`). New strategies
(flexible window, no overtime on late days, shift-based, ...) are added by implementing
`CalculationPolicy` and registering it in `CALCULATION_POLICIES`.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Protocol

from app.core.config.policy import WEEKDAY_CODES, AttendancePolicy
from app.domain.attendance.models import DayStatus
from app.domain.attendance.timesheet import SheetDay, sheet_day
from app.domain.users.models import SCHEDULE_HOURLY


@dataclass(frozen=True)
class WorkSchedule:
    """The expected working day for one employee on one date."""

    code: str
    is_workday: bool
    start: time
    end: time
    required_minutes: int
    allowed_late_minutes: int
    allowed_early_minutes: int
    overtime_rounding_minutes: int
    floating_until: time | None = None
    hourly: bool = False


@dataclass(frozen=True)
class DailyCalculation:
    status: DayStatus
    check_in: datetime | None
    check_out: datetime | None
    gross_worked_minutes: int | None
    required_minutes: int
    late_minutes: int
    early_departure_minutes: int
    allowed_late_minutes: int
    allowed_early_minutes: int
    raw_balance_minutes: int | None
    overtime_minutes: int
    deficit_minutes: int
    net_balance_minutes: int | None
    # Lateness inside the floating check-in window (تأخیر مجاز). late_minutes is the unauthorized part.
    floating_late_minutes: int = 0
    # Presence inside the official window (کارکرد عادی, column K of the timesheet)
    normal_minutes: int = 0
    # Hourly staff: only check-in, check-out and worked minutes apply
    hourly: bool = False


class CalculationPolicy(Protocol):
    name: str

    def calculate(
        self, schedule: WorkSchedule, check_in: datetime | None, check_out: datetime | None
    ) -> DailyCalculation: ...


def _minutes_between(start: datetime, end: datetime) -> int:
    return int((end - start).total_seconds() // 60)


def round_down(minutes: int, step: int) -> int:
    if step <= 1 or minutes <= 0:
        return max(0, minutes)
    return (minutes // step) * step


class StandardDeductionPolicy:
    """The default formula from the organization brief.

        gross   = check_out - check_in
        late    = max(0, check_in - start - grace_late)
        early   = max(0, end - check_out - grace_early)
        raw     = gross - required
        deficit = max(0, -raw)
        overtime= round_down(max(0, raw - late - early))
        net     = raw - late - early

    `check_in` and `check_out` must be local (organization timezone) aware datetimes.
    """

    name = "standard_deduction"

    def calculate(
        self, schedule: WorkSchedule, check_in: datetime | None, check_out: datetime | None
    ) -> DailyCalculation:
        base = {
            "check_in": check_in,
            "check_out": check_out,
            "required_minutes": schedule.required_minutes,
            "allowed_late_minutes": schedule.allowed_late_minutes,
            "allowed_early_minutes": schedule.allowed_early_minutes,
        }
        if check_in is None and check_out is None:
            status = DayStatus.ABSENT if schedule.is_workday else DayStatus.NON_WORKDAY
            deficit = schedule.required_minutes if schedule.is_workday else 0
            return DailyCalculation(
                status=status,
                gross_worked_minutes=None,
                late_minutes=0,
                early_departure_minutes=0,
                raw_balance_minutes=-deficit if schedule.is_workday else None,
                overtime_minutes=0,
                deficit_minutes=deficit,
                net_balance_minutes=-deficit if schedule.is_workday else None,
                **base,
            )
        if check_in is None or check_out is None:
            status = DayStatus.MISSING_CHECK_OUT if check_out is None else DayStatus.MISSING_CHECK_IN
            late = self._late(schedule, check_in) if check_in else 0
            return DailyCalculation(
                status=status,
                gross_worked_minutes=None,
                late_minutes=late,
                early_departure_minutes=0,
                raw_balance_minutes=None,
                overtime_minutes=0,
                deficit_minutes=0,
                net_balance_minutes=None,
                **base,
            )

        gross = _minutes_between(check_in, check_out)
        late = self._late(schedule, check_in)
        early = self._early(schedule, check_in.date(), check_out)
        raw = gross - schedule.required_minutes
        return DailyCalculation(
            status=DayStatus.COMPLETE,
            gross_worked_minutes=gross,
            late_minutes=late,
            early_departure_minutes=early,
            raw_balance_minutes=raw,
            overtime_minutes=round_down(raw - late - early, schedule.overtime_rounding_minutes),
            deficit_minutes=max(0, -raw),
            net_balance_minutes=raw - late - early,
            **base,
        )

    @staticmethod
    def _late(schedule: WorkSchedule, check_in: datetime) -> int:
        if not schedule.is_workday:
            return 0
        expected = datetime.combine(check_in.date(), schedule.start, tzinfo=check_in.tzinfo)
        return max(0, _minutes_between(expected, check_in) - schedule.allowed_late_minutes)

    @staticmethod
    def _early(schedule: WorkSchedule, work_date: date, check_out: datetime) -> int:
        if not schedule.is_workday:
            return 0
        expected = datetime.combine(work_date, schedule.end, tzinfo=check_out.tzinfo)
        return max(0, _minutes_between(check_out, expected) - schedule.allowed_early_minutes)


class FloatingWindowPolicy:
    """The organization's timesheet: the same formulas as its Excel workbook (see timesheet.py).

    floating_late = L تاخیر مجاز       late  = M تاخیر غیر مجاز     early = N تعجیل
    normal        = K کارکرد عادی       gross = J ساعت مفید          overtime = O اضافه کار
    deficit       = required - K: the part of the official window the person was not there
    """

    name = "floating_window"

    def calculate(
        self, schedule: WorkSchedule, check_in: datetime | None, check_out: datetime | None
    ) -> DailyCalculation:
        base = {
            "check_in": check_in,
            "check_out": check_out,
            "required_minutes": schedule.required_minutes,
            "allowed_late_minutes": schedule.allowed_late_minutes,
            "allowed_early_minutes": schedule.allowed_early_minutes,
        }
        if check_in is None and check_out is None:
            # Absent and non-working days look the same as in the standard policy.
            return StandardDeductionPolicy().calculate(schedule, None, None)
        if check_in is None or check_out is None:
            # Incomplete: only the lateness of a registered check-in is known yet.
            day = self.sheet_day(schedule, check_in, None) if check_in else None
            return DailyCalculation(
                status=DayStatus.MISSING_CHECK_OUT if check_out is None else DayStatus.MISSING_CHECK_IN,
                gross_worked_minutes=None,
                late_minutes=day.late if day else 0,
                floating_late_minutes=day.floating_late if day else 0,
                early_departure_minutes=0,
                raw_balance_minutes=None,
                overtime_minutes=0,
                deficit_minutes=0,
                net_balance_minutes=None,
                **base,
            )
        day = self.sheet_day(schedule, check_in, check_out)
        raw = day.useful - schedule.required_minutes
        return DailyCalculation(
            status=DayStatus.COMPLETE,
            gross_worked_minutes=day.useful,
            normal_minutes=day.normal,
            late_minutes=day.late,
            floating_late_minutes=day.floating_late,
            early_departure_minutes=day.early,
            raw_balance_minutes=raw,
            overtime_minutes=day.overtime,
            deficit_minutes=max(0, schedule.required_minutes - day.normal),
            net_balance_minutes=raw,
            **base,
        )

    @staticmethod
    def sheet_day(schedule: WorkSchedule, check_in: datetime | None, check_out: datetime | None) -> SheetDay:
        """Run the timesheet formulas on minutes after midnight of the work date."""
        anchor = check_in or check_out
        assert anchor is not None
        midnight = datetime.combine(anchor.date(), time(0, 0), tzinfo=anchor.tzinfo)

        def minutes(value: datetime | None) -> int | None:
            return _minutes_between(midnight, value) if value else None

        d, h = minutes(check_in), minutes(check_out)
        e, f, g = sheet_window(schedule.is_workday, d, schedule)
        return sheet_day(d, h, e, f, g)


def sheet_window(is_workday: bool, check_in: int | None, schedule: WorkSchedule) -> tuple[int, int, int]:
    """E, F, G of the timesheet row (شروع شناوری, پایان شناوری, ساعت مجاز خروج).

    On a day off (Friday, Thursday for most, holidays) all presence is overtime. The formulas stay
    the same; E = F = G = the check-in time makes them give exactly that: no normal work, no
    lateness, no early departure, and overtime = check-out − check-in.
    """
    if not is_workday and check_in:
        return check_in, check_in, check_in

    def clock(value: time) -> int:
        return value.hour * 60 + value.minute

    return clock(schedule.start), clock(schedule.floating_until or schedule.start), clock(schedule.end)


class HourlyPolicy:
    """Hourly staff have no fixed hours: no lateness, early departure, overtime or deficit."""

    name = "hourly"

    def calculate(
        self, schedule: WorkSchedule, check_in: datetime | None, check_out: datetime | None
    ) -> DailyCalculation:
        if check_in and check_out:
            status = DayStatus.COMPLETE
        elif check_in:
            status = DayStatus.MISSING_CHECK_OUT
        elif check_out:
            status = DayStatus.MISSING_CHECK_IN
        else:
            status = DayStatus.NON_WORKDAY  # not coming in is not an absence for hourly staff
        return DailyCalculation(
            status=status,
            check_in=check_in,
            check_out=check_out,
            gross_worked_minutes=_minutes_between(check_in, check_out) if check_in and check_out else None,
            required_minutes=0,
            late_minutes=0,
            early_departure_minutes=0,
            allowed_late_minutes=0,
            allowed_early_minutes=0,
            raw_balance_minutes=None,
            overtime_minutes=0,
            deficit_minutes=0,
            net_balance_minutes=None,
            hourly=True,
        )


HOURLY_POLICY = HourlyPolicy()

CALCULATION_POLICIES: dict[str, CalculationPolicy] = {
    StandardDeductionPolicy.name: StandardDeductionPolicy(),
    FloatingWindowPolicy.name: FloatingWindowPolicy(),
}


def get_calculation_policy(name: str) -> CalculationPolicy:
    try:
        return CALCULATION_POLICIES[name]
    except KeyError:
        raise ValueError(f"unknown attendance calculation policy {name!r}") from None


class ScheduleResolver:
    """Returns the schedule for an employee-day.

    One organization-wide schedule; a person may have their own workdays (`User.workdays`) and
    hourly staff their own calculation (`User.schedule_code`). Holidays apply to everyone.
    """

    def __init__(self, policy: AttendancePolicy, holidays: Mapping[date, str] | set[date]):
        self.policy = policy
        self.holidays = holidays

    def holiday_name(self, day: date) -> str | None:
        if day not in self.holidays:
            return None
        return self.holidays.get(day, "تعطیل") if isinstance(self.holidays, Mapping) else "تعطیل"

    def is_workday(self, day: date, workday_codes: list[str] | None = None) -> bool:
        weekdays = frozenset(WEEKDAY_CODES[c] for c in workday_codes) if workday_codes else self.policy.workday_numbers
        return day.weekday() in weekdays and day not in self.holidays

    def for_day(
        self, day: date, schedule_code: str | None = None, workday_codes: list[str] | None = None
    ) -> WorkSchedule:
        workday = self.is_workday(day, workday_codes) and schedule_code != SCHEDULE_HOURLY
        return WorkSchedule(
            code=schedule_code or "default",
            is_workday=workday,
            start=self.policy.standard_start_time,
            end=self.policy.standard_end_time,
            required_minutes=self.policy.standard_workday_minutes if workday else 0,
            allowed_late_minutes=self.policy.allowed_late_arrival_minutes,
            allowed_early_minutes=self.policy.allowed_early_departure_minutes,
            overtime_rounding_minutes=self.policy.overtime_rounding_minutes,
            floating_until=self.policy.floating_check_in_until,
            hourly=schedule_code == SCHEDULE_HOURLY,
        )
