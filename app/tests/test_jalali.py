from datetime import date, datetime, time

import pytest

from app.core.utils.clock import FixedClock, TimeService
from app.core.utils.jalali import (
    JalaliDate,
    days_in_month,
    format_date,
    format_date_short,
    month_range,
    previous_month_range,
    to_gregorian,
    to_jalali,
    week_range,
    week_start,
)
from app.core.utils.text import format_minutes, format_money, normalize_digits, to_persian_digits
from app.tests.conftest import tehran


@pytest.mark.parametrize(
    ("gregorian", "jalali"),
    [
        (date(2026, 3, 21), (1405, 1, 1)),  # Nowruz 1405
        (date(2026, 3, 20), (1404, 12, 29)),  # last day of non-leap 1404
        (date(2025, 3, 20), (1403, 12, 30)),  # leap day of 1403
        (date(2026, 10, 19), (1405, 7, 27)),
        (date(2026, 9, 22), (1405, 6, 31)),  # last day of the 31-day months
        (date(2026, 9, 23), (1405, 7, 1)),
    ],
)
def test_round_trip_boundaries(gregorian: date, jalali: tuple[int, int, int]) -> None:
    assert to_jalali(gregorian) == JalaliDate(*jalali)
    assert to_gregorian(*jalali) == gregorian


def test_month_lengths_and_leap_years() -> None:
    assert days_in_month(1405, 6) == 31
    assert days_in_month(1405, 7) == 30
    assert days_in_month(1403, 12) == 30  # leap
    assert days_in_month(1404, 12) == 29
    with pytest.raises(ValueError):
        JalaliDate(1404, 12, 30).to_gregorian()


def test_parse_accepts_persian_digits_and_rejects_garbage() -> None:
    assert JalaliDate.parse("۱۴۰۵/۰۷/۲۷").to_gregorian() == date(2026, 10, 19)
    assert JalaliDate.parse("1405-7-27") == JalaliDate(1405, 7, 27)
    for bad in ("1405/13/01", "1405/07/31", "27/07/1405", "abc", "1405/07"):
        with pytest.raises(ValueError):
            JalaliDate.parse(bad)


def test_week_starts_on_saturday() -> None:
    monday = date(2026, 10, 19)
    assert week_start(monday) == date(2026, 10, 17)
    assert week_start(date(2026, 10, 17)) == date(2026, 10, 17)  # Saturday itself
    assert week_range(date(2026, 10, 23)) == (date(2026, 10, 17), date(2026, 10, 23))  # Friday ends it


def test_month_ranges() -> None:
    assert month_range(date(2026, 10, 19)) == (date(2026, 9, 23), date(2026, 10, 22))
    assert previous_month_range(date(2026, 10, 19)) == (date(2026, 8, 23), date(2026, 9, 22))
    # Esfand of a leap year
    first, last = month_range(date(2025, 3, 1))
    assert to_jalali(first) == JalaliDate(1403, 12, 1) and to_jalali(last) == JalaliDate(1403, 12, 30)


def test_formatting() -> None:
    assert format_date(date(2026, 10, 19)) == "دوشنبه ۲۷ مهر ۱۴۰۵"
    assert format_date_short(date(2026, 10, 17)) == "شنبه ۲۵/۷"
    assert to_persian_digits("08:45") == "۰۸:۴۵"
    assert normalize_digits("۰۸:۴۵ ٣") == "08:45 3"
    assert format_minutes(125) == "۲:۰۵"
    assert format_minutes(-30, signed=True) == "-۰:۳۰"
    assert format_minutes(30, signed=True) == "+۰:۳۰"
    assert format_money(1250000, "تومان") == "۱٬۲۵۰٬۰۰۰ تومان"


def test_time_service_uses_tehran_not_server_time() -> None:
    # 22:00 UTC on 2026-10-18 is already 01:30 on the 19th in Tehran (UTC+3:30, no DST since 1401).
    clock = FixedClock(datetime.fromisoformat("2026-10-18T22:00:00+00:00"))
    ts = TimeService(clock)
    assert ts.today() == date(2026, 10, 19)
    assert ts.local_to_utc(date(2026, 10, 19), time(9, 0)).isoformat() == "2026-10-19T05:30:00+00:00"
    start, end = ts.local_day_bounds_utc(date(2026, 10, 19))
    assert (end - start).total_seconds() == 86400
    assert ts.to_local(tehran(2026, 10, 19, 9, 0)).hour == 9
