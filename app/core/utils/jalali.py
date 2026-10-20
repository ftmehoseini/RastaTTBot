"""The single place for Jalali (Shamsi) calendar conversion and Persian date formatting."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import jdatetime

from app.core.utils.text import to_persian_digits

MONTH_NAMES: tuple[str, ...] = (
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
)  # fmt: skip

# Keyed by Python's date.weekday() (Monday = 0).
WEEKDAY_NAMES: dict[int, str] = {
    5: "شنبه", 6: "یکشنبه", 0: "دوشنبه", 1: "سه‌شنبه", 2: "چهارشنبه", 3: "پنجشنبه", 4: "جمعه",
}  # fmt: skip

SATURDAY = 5


@dataclass(frozen=True, order=True)
class JalaliDate:
    year: int
    month: int
    day: int

    @classmethod
    def from_gregorian(cls, value: date) -> "JalaliDate":
        j = jdatetime.date.fromgregorian(date=value)
        return cls(j.year, j.month, j.day)

    @classmethod
    def parse(cls, text: str) -> "JalaliDate":
        """Parse 'YYYY/MM/DD' or 'YYYY-MM-DD' (ASCII or Persian digits). Raises ValueError."""
        from app.core.utils.text import normalize_digits

        cleaned = normalize_digits(text.strip()).replace("-", "/")
        parts = cleaned.split("/")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            raise ValueError(f"not a Jalali date: {text!r}")
        year, month, day = (int(p) for p in parts)
        if year < 1300 or year > 1500:
            raise ValueError(f"Jalali year out of range: {text!r}")
        result = cls(year, month, day)
        result.to_gregorian()  # validates month/day
        return result

    def to_gregorian(self) -> date:
        if not 1 <= self.month <= 12 or not 1 <= self.day <= days_in_month(self.year, self.month):
            raise ValueError(f"invalid Jalali date {self.year}/{self.month}/{self.day}")
        return jdatetime.date(self.year, self.month, self.day).togregorian()

    def iso(self) -> str:
        return f"{self.year:04d}/{self.month:02d}/{self.day:02d}"


def is_leap_year(year: int) -> bool:
    return jdatetime.date(year, 1, 1).isleap()


def days_in_month(year: int, month: int) -> int:
    if month <= 6:
        return 31
    if month <= 11:
        return 30
    return 30 if is_leap_year(year) else 29


def to_jalali(value: date) -> JalaliDate:
    return JalaliDate.from_gregorian(value)


def to_gregorian(year: int, month: int, day: int) -> date:
    return JalaliDate(year, month, day).to_gregorian()


def week_start(value: date) -> date:
    """The Saturday that starts value's Iranian week."""
    return value - timedelta(days=(value.weekday() - SATURDAY) % 7)


def week_range(value: date) -> tuple[date, date]:
    start = week_start(value)
    return start, start + timedelta(days=6)


def month_range(value: date) -> tuple[date, date]:
    """First and last Gregorian dates of value's Jalali month."""
    j = to_jalali(value)
    return to_gregorian(j.year, j.month, 1), to_gregorian(j.year, j.month, days_in_month(j.year, j.month))


def previous_month_range(value: date) -> tuple[date, date]:
    first, _ = month_range(value)
    return month_range(first - timedelta(days=1))


def iter_days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def weekday_name(value: date) -> str:
    return WEEKDAY_NAMES[value.weekday()]


def month_name(month: int) -> str:
    return MONTH_NAMES[month - 1]


def format_date(value: date, persian_digits: bool = True) -> str:
    """'دوشنبه ۲۸ مهر ۱۴۰۵'"""
    j = to_jalali(value)
    text = f"{weekday_name(value)} {j.day} {month_name(j.month)} {j.year}"
    return to_persian_digits(text) if persian_digits else text


def format_date_numeric(value: date, persian_digits: bool = True) -> str:
    """'۱۴۰۵/۰۷/۲۸'"""
    text = to_jalali(value).iso()
    return to_persian_digits(text) if persian_digits else text


def format_date_short(value: date, persian_digits: bool = True) -> str:
    """'شنبه ۲/۷' (weekday day/month), the style used in food menus."""
    j = to_jalali(value)
    text = f"{weekday_name(value)} {j.day}/{j.month}"
    return to_persian_digits(text) if persian_digits else text


def format_time(value: datetime, persian_digits: bool = True) -> str:
    text = value.strftime("%H:%M")
    return to_persian_digits(text) if persian_digits else text


def format_datetime(value: datetime, persian_digits: bool = True) -> str:
    return f"{format_date(value.date(), persian_digits)} ساعت {format_time(value, persian_digits)}"


def month_label(year: int, month: int, persian_digits: bool = True) -> str:
    text = f"{month_name(month)} {year}"
    return to_persian_digits(text) if persian_digits else text
