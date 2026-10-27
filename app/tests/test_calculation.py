from datetime import date

import pytest

from app.core.config.policy import AttendancePolicy
from app.domain.attendance.calculation import (
    FloatingWindowPolicy,
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


# --- floating_window: the organization's rules (08:00–16:48, floating until 09:30) -------------


@pytest.fixture
def floating() -> AttendancePolicy:
    return AttendancePolicy(
        standard_start_time="08:00",
        standard_end_time="16:48",
        standard_workday_minutes=528,
        floating_check_in_until="09:30",
        overtime_rounding_minutes=0,
        calculation_policy="floating_window",
    )


def fcalc(policy: AttendancePolicy, check_in, check_out):
    schedule = ScheduleResolver(policy, set()).for_day(MONDAY)
    return FloatingWindowPolicy().calculate(schedule, check_in, check_out)


def test_floating_on_time(floating: AttendancePolicy) -> None:
    c = fcalc(floating, tehran(2026, 10, 19, 8, 0), tehran(2026, 10, 19, 16, 48))
    assert (c.floating_late_minutes, c.late_minutes, c.early_departure_minutes) == (0, 0, 0)
    assert (c.overtime_minutes, c.deficit_minutes, c.net_balance_minutes) == (0, 0, 0)


def test_floating_arrival_inside_window_is_allowed(floating: AttendancePolicy) -> None:
    # In 09:00, out 17:48 → 1 h allowed lateness; presence after 16:48 is overtime and the missed
    # 08:00–09:00 is a deficit, exactly as in the organization's Excel timesheet.
    c = fcalc(floating, tehran(2026, 10, 19, 9, 0), tehran(2026, 10, 19, 17, 48))
    assert (c.floating_late_minutes, c.late_minutes, c.early_departure_minutes) == (60, 0, 0)
    assert (c.gross_worked_minutes, c.normal_minutes, c.overtime_minutes, c.deficit_minutes) == (528, 468, 60, 60)


def test_floating_unauthorized_late_and_early(floating: AttendancePolicy) -> None:
    # In 09:45, out 16:30 → 1:30 allowed, 0:15 unauthorized, 0:18 early, worked 6:45
    c = fcalc(floating, tehran(2026, 10, 19, 9, 45), tehran(2026, 10, 19, 16, 30))
    assert (c.floating_late_minutes, c.late_minutes, c.early_departure_minutes) == (90, 15, 18)
    assert (c.gross_worked_minutes, c.normal_minutes, c.overtime_minutes, c.deficit_minutes) == (405, 405, 0, 123)


def test_floating_overtime_is_presence_outside_the_official_window(floating: AttendancePolicy) -> None:
    # In 07:30, out 18:00 → 30 min before 08:00 + 72 min after 16:48
    c = fcalc(floating, tehran(2026, 10, 19, 7, 30), tehran(2026, 10, 19, 18, 0))
    assert (c.floating_late_minutes, c.late_minutes, c.early_departure_minutes) == (0, 0, 0)
    assert (c.normal_minutes, c.overtime_minutes, c.deficit_minutes) == (528, 102, 0)


@pytest.mark.parametrize(
    ("check_in", "check_out", "expected"),
    [
        # Rows of the organization's own timesheet: (J, K, L, M, N, O) as Excel calculated them
        ((8, 38), (17, 6), (508, 490, 38, 0, 0, 18)),
        ((11, 34), (19, 0), (446, 314, 90, 124, 0, 132)),
        ((9, 25), (16, 27), (422, 422, 85, 0, 21, 0)),
        ((8, 50), (18, 45), (595, 478, 50, 0, 0, 117)),
    ],
)
def test_matches_the_excel_timesheet(floating: AttendancePolicy, check_in, check_out, expected) -> None:
    c = fcalc(floating, tehran(2026, 10, 19, *check_in), tehran(2026, 10, 19, *check_out))
    got = (
        c.gross_worked_minutes,
        c.normal_minutes,
        c.floating_late_minutes,
        c.late_minutes,
        c.early_departure_minutes,
        c.overtime_minutes,
    )
    assert got == expected


def test_floating_missing_check_out_still_shows_lateness(floating: AttendancePolicy) -> None:
    c = fcalc(floating, tehran(2026, 10, 19, 10, 0), None)
    assert c.status == DayStatus.MISSING_CHECK_OUT
    assert (c.floating_late_minutes, c.late_minutes) == (90, 30)


def test_floating_non_workday_uses_the_same_formulas_and_absence(floating: AttendancePolicy) -> None:
    # The Excel formulas do not look at the weekday, so neither does the bot (Friday 10:00–12:00).
    schedule = ScheduleResolver(floating, set()).for_day(FRIDAY)
    c = FloatingWindowPolicy().calculate(schedule, tehran(2026, 10, 23, 10, 0), tehran(2026, 10, 23, 12, 0))
    assert (c.floating_late_minutes, c.late_minutes, c.normal_minutes, c.overtime_minutes) == (90, 30, 120, 0)
    assert c.deficit_minutes == 0  # nothing is required on a Friday
    absent = fcalc(floating, None, None)
    assert absent.status == DayStatus.ABSENT and absent.deficit_minutes == 528


def test_floating_window_must_be_inside_the_day() -> None:
    with pytest.raises(ValueError):
        AttendancePolicy(standard_start_time="08:00", standard_end_time="16:48", floating_check_in_until="07:00")
