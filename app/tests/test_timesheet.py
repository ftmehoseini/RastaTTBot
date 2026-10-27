"""The monthly summary block of the organization's Excel timesheet."""

from app.domain.attendance.timesheet import SheetMonth, SheetRules, sheet_day, sheet_month

RULES = SheetRules(floating_start=480, floating_end=570, allowed_exit=1008, late_cap=420, early_cap=180)


def hm(hours: int, minutes: int = 0, days: int = 0) -> int:
    return days * 1440 + hours * 60 + minutes


def test_month_totals_of_a_real_sheet() -> None:
    # Totals of one month of the organization's workbook; the expected values are the ones Excel
    # calculated (that month's کارکرد نهایی had no 0.8 / 1.6 factors).
    month = SheetMonth(
        normal=hm(0, 56, days=6),
        overtime=hm(14, 8, days=1),
        floating_late=hm(20, 32),
        late=hm(4, 38),
        early=hm(0, 54),
        paid_leave=hm(13, 48),
        late_cap=420,
        early_cap=180,
        surplus_factor=0,
        penalty_factor=0,
    )
    assert month.compensable == hm(2, 4, days=1)
    assert month.overtime_surplus == hm(12, 4)
    assert month.uncompensated == 0
    assert month.final_work == hm(16, 48, days=7)


def test_caps_split_compensable_and_non_compensable() -> None:
    month = SheetMonth(
        normal=hm(150),
        overtime=hm(5),
        floating_late=hm(2),
        late=hm(8, 20),
        early=hm(3, 20),
        paid_leave=0,
        late_cap=420,
        early_cap=180,
    )
    assert (month.late_over_cap, month.early_over_cap) == (80, 20)
    assert month.compensable == hm(2) + hm(7) + hm(3)
    assert month.non_compensable == 100
    assert month.overtime_surplus == 0
    assert month.uncompensated == (hm(12) - hm(5)) + 100  # what overtime didn't cover + over the caps
    assert month.final_work == round(hm(150) + hm(5) - month.uncompensated * 1.6)


def test_surplus_overtime_counts_at_its_factor() -> None:
    days = [sheet_day(hm(8, 0), hm(18, 48), 480, 570, 1008) for _ in range(3)]  # 2 h overtime a day
    month = sheet_month(days, RULES)
    assert (month.overtime, month.compensable, month.overtime_surplus) == (hm(6), 0, hm(6))
    assert month.final_work == round(3 * 528 + 0 + hm(6) * 0.8)


def test_empty_cells_behave_like_excel() -> None:
    assert sheet_day(None, None, 480, 570, 1008) == sheet_day(0, 0, 480, 570, 1008)
    absent = sheet_day(None, None, 480, 570, 1008)
    assert (absent.normal, absent.floating_late, absent.late, absent.early, absent.overtime) == (0, 0, 0, 0, 0)
    # A check-out after midnight is just larger than 24 h, as a time over one day is in Excel.
    night = sheet_day(hm(20), hm(26), 480, 570, 1008)
    assert night.useful == hm(6)
