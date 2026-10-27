"""Reusable Jalali date picker and time picker.

The calendar shows one Jalali month at a time with previous/next navigation, so any year and
month inside the allowed range is reachable. Days outside [min_date, max_date] are shown but
not clickable. The allowed range is stored in FSM data by the flow that opens the picker.
"""

from datetime import date, timedelta

from aiogram.types import InlineKeyboardMarkup

from app.core.utils.jalali import month_name, to_jalali, weekday_name
from app.core.utils.text import to_persian_digits
from app.presentation.bot.callbacks.data import CalCb, KeyCb, TimeCb
from app.presentation.bot.keyboards.common import button, chunk, inline, step_row

# A compact day list instead of a 7-column month grid: Bale/Soroush clients fail to render wide
# grids padded with placeholder buttons, while a short list of real dates works everywhere.
PAGE_DAYS = 8
MINUTES = (0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55)
TYPE_DATE_HINT = "یا تاریخ را بنویسید، مثل ۱۴۰۵/۰۷/۲۶"


def day_label(day: date) -> str:
    j = to_jalali(day)
    return f"{weekday_name(day)} {to_persian_digits(j.day)} {month_name(j.month)}"


def day_page(
    purpose: str, page_start: date, min_date: date, max_date: date, back: str | None = None
) -> InlineKeyboardMarkup:
    page_start = min(max(page_start, min_date), max_date)
    days = [page_start + timedelta(days=i) for i in range(PAGE_DAYS) if page_start + timedelta(days=i) <= max_date]
    buttons = [button(day_label(d), CalCb(p=purpose, a="d", v=d.toordinal()).pack()) for d in days]
    rows = chunk(buttons, 2)
    nav = []
    if page_start > min_date:
        previous = max(min_date, page_start - timedelta(days=PAGE_DAYS))
        nav.append(button("◀️ روزهای قبل", CalCb(p=purpose, a="m", v=previous.toordinal()).pack()))
    if days and days[-1] < max_date:
        following = days[-1] + timedelta(days=1)
        nav.append(button("روزهای بعد ▶️", CalCb(p=purpose, a="m", v=following.toordinal()).pack()))
    if nav:
        rows.append(nav)
    rows.append(step_row(back))
    return inline(rows)


def calendar_for(
    purpose: str, around: date, min_date: date, max_date: date, back: str | None = None
) -> InlineKeyboardMarkup:
    """First page of the day picker, chosen so that `around` (usually today) is on it."""
    anchor = min(max(around, min_date), max_date)
    if anchor + timedelta(days=PAGE_DAYS - 1) > max_date:
        # Near the end of the window (e.g. past dates for corrections): show the days up to anchor.
        anchor = max(min_date, anchor - timedelta(days=PAGE_DAYS - 1))
    return day_page(purpose, anchor, min_date, max_date, back)


def keypad_digits(typed: str) -> list[int]:
    """The digits that can come next in a 24-hour HH:MM time."""
    position = len(typed)
    if position == 0:
        return [0, 1, 2]
    if position == 1:
        return list(range(4)) if typed == "2" else list(range(10))
    if position == 2:
        return list(range(6))
    if position == 3:
        return list(range(10))
    return []


def keypad_display(typed: str) -> str:
    """'14:3_' for the digits typed so far."""
    padded = (typed + "____")[:4]
    return to_persian_digits(f"{padded[:2]}:{padded[2:]}")


def time_keypad(purpose: str, typed: str, back: str | None = None) -> InlineKeyboardMarkup:
    """Pick the four digits of HH:MM one at a time; only valid digits are offered.

    A grid of four 0–9 columns would need rows of four buttons, which Soroush rejects (two per
    row at most), so the columns are filled in turn instead.
    """
    digits = [button(to_persian_digits(d), KeyCb(p=purpose, v=typed, d=d).pack()) for d in keypad_digits(typed)]
    rows = chunk(digits, 5)
    if typed:
        rows.append([button("⌫ پاک کردن رقم", KeyCb(p=purpose, v=typed, a="del").pack())])
    rows.append(step_row(back))
    return inline(rows)


def hour_picker(
    purpose: str, first_hour: int = 0, last_hour: int = 23, back: str | None = None
) -> InlineKeyboardMarkup:
    hours = [
        button(to_persian_digits(f"{h:02d}"), TimeCb(p=purpose, a="h", h=h).pack())
        for h in range(first_hour, last_hour + 1)
    ]
    return inline([*chunk(hours, 6), step_row(back)])


def minute_picker(purpose: str, hour: int, back: str | None = None) -> InlineKeyboardMarkup:
    minutes = [
        button(to_persian_digits(f"{hour:02d}:{m:02d}"), TimeCb(p=purpose, a="m", h=hour, m=m).pack()) for m in MINUTES
    ]
    return inline([*chunk(minutes, 4), step_row(back)])
