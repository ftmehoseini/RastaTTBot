from datetime import date

import pytest

from app.core.config.policy import AttendancePolicy
from app.domain.attendance.calculation import (
    ScheduleResolver,
    StandardDeductionPolicy,
    get_calculation_policy,
    round_down,
)
from app.domain.attendance.models import DayStatus
from app.tests.conftest import tehran

MONDAY = date(2026, 10, 19)
FRIDAY = date(2026, 10, 23)


def calc(policy: AttendancePolicy, day: date, check_in, check_out, holidays: set[date] | None = None):
    schedule = ScheduleResolver(policy, holidays or set()).for_day(day)
    return StandardDeductionPolicy().calculate(schedule, check_in, check_out)


@pytest.fixture
def policy() -> AttendancePolicy:
    return AttendancePolicy()  # 09:00–17:00, 480 min, no grace, 15-min overtime rounding


def test_exact_day(policy: AttendancePolicy) -> None:
    c = calc(policy, MONDAY, tehran(2026, 10, 19, 9, 0), tehran(2026, 10, 19, 17, 0))
    assert c.status == DayStatus.COMPLETE
    assert (c.gross_worked_minutes, c.late_minutes, c.early_departure_minutes) == (480, 0, 0)
    assert (c.raw_balance_minutes, c.overtime_minutes, c.deficit_minutes, c.net_balance_minutes) == (0, 0, 0, 0)


def test_late_and_early(policy: AttendancePolicy) -> None:
    c = calc(policy, MONDAY, tehran(2026, 10, 19, 9, 20), tehran(2026, 10, 19, 16, 30))
    assert c.gross_worked_minutes == 430
    assert c.late_minutes == 20
    assert c.early_departure_minutes == 30
    assert c.raw_balance_minutes == -50
    assert c.deficit_minutes == 50
    assert c.overtime_minutes == 0
    assert c.net_balance_minutes == -100  # raw - late - early


def test_overtime_after_late_arrival_is_reduced_and_rounded(policy: AttendancePolicy) -> None:
    # In 09:10, out 18:40 → gross 570, raw +90, late 10 → overtime 80 → rounded down to 75
    c = calc(policy, MONDAY, tehran(2026, 10, 19, 9, 10), tehran(2026, 10, 19, 18, 40))
    assert c.raw_balance_minutes == 90
    assert c.late_minutes == 10
    assert c.overtime_minutes == 75
    assert c.net_balance_minutes == 80


def test_grace_minutes() -> None:
    policy = AttendancePolicy(allowed_late_arrival_minutes=15, allowed_early_departure_minutes=10)
    c = calc(policy, MONDAY, tehran(2026, 10, 19, 9, 12), tehran(2026, 10, 19, 16, 45))
    assert c.late_minutes == 0  # 12 ≤ 15 grace
    assert c.early_departure_minutes == 5  # 15 early - 10 grace
    assert (c.allowed_late_minutes, c.allowed_early_minutes) == (15, 10)


def test_early_arrival_is_not_negative_lateness(policy: AttendancePolicy) -> None:
    c = calc(policy, MONDAY, tehran(2026, 10, 19, 7, 30), tehran(2026, 10, 19, 17, 0))
    assert c.late_minutes == 0
    assert c.overtime_minutes == 90


def test_non_workday_counts_everything_as_overtime(policy: AttendancePolicy) -> None:
    c = calc(policy, FRIDAY, tehran(2026, 10, 23, 10, 0), tehran(2026, 10, 23, 13, 7))
    assert c.required_minutes == 0
    assert c.late_minutes == 0 and c.early_departure_minutes == 0
    assert c.overtime_minutes == 180  # 187 rounded down to 15
    assert c.net_balance_minutes == 187


def test_holiday_is_not_a_workday(policy: AttendancePolicy) -> None:
    c = calc(policy, MONDAY, None, None, holidays={MONDAY})
    assert c.status == DayStatus.NON_WORKDAY
    assert c.deficit_minutes == 0


def test_absent_and_incomplete_days(policy: AttendancePolicy) -> None:
    absent = calc(policy, MONDAY, None, None)
    assert absent.status == DayStatus.ABSENT
    assert absent.deficit_minutes == 480 and absent.net_balance_minutes == -480
    open_day = calc(policy, MONDAY, tehran(2026, 10, 19, 9, 30), None)
    assert open_day.status == DayStatus.MISSING_CHECK_OUT
    assert open_day.late_minutes == 30
    assert open_day.gross_worked_minutes is None and open_day.net_balance_minutes is None


def test_night_shift_crossing_midnight(policy: AttendancePolicy) -> None:
    c = calc(policy, MONDAY, tehran(2026, 10, 19, 20, 0), tehran(2026, 10, 20, 4, 0))
    assert c.gross_worked_minutes == 480
    assert c.early_departure_minutes == 0  # left "after" 17:00 of the work date


def test_round_down_and_registry() -> None:
    assert round_down(29, 15) == 15
    assert round_down(-10, 15) == 0
    assert round_down(29, 0) == 29
    assert get_calculation_policy("standard_deduction").name == "standard_deduction"
    with pytest.raises(ValueError):
        get_calculation_policy("unknown")
