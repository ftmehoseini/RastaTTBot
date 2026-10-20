"""Reusable Jalali date picker and time picker.

The calendar shows one Jalali month at a time with previous/next navigation, so any year and
month inside the allowed range is reachable. Days outside [min_date, max_date] are shown but
not clickable. The allowed range is stored in FSM data by the flow that opens the picker.
"""

from datetime import date, timedelta

from aiogram.types import InlineKeyboardMarkup

from app.core.utils.jalali import days_in_month, month_label, to_gregorian, to_jalali
from app.core.utils.text import to_persian_digits
from app.presentation.bot.callbacks.data import CalCb, TimeCb
from app.presentation.bot.keyboards.common import button, cancel_row, chunk, inline, noop

WEEK_HEADER = ["ش", "ی", "د", "س", "چ", "پ", "ج"]
# Placeholder for empty calendar cells: platforms reject buttons whose text is blank.
BLANK = "·"
MINUTES = (0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55)


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def calendar(purpose: str, year: int, month: int, min_date: date, max_date: date) -> InlineKeyboardMarkup:
    rows = []
    min_j, max_j = to_jalali(min_date), to_jalali(max_date)
    prev_y, prev_m = _shift_month(year, month, -1)
    next_y, next_m = _shift_month(year, month, 1)
    has_prev = (prev_y, prev_m) >= (min_j.year, min_j.month)
    has_next = (next_y, next_m) <= (max_j.year, max_j.month)
    rows.append(
        [
            button("◀️", CalCb(p=purpose, a="m", v=prev_y * 100 + prev_m).pack()) if has_prev else noop(BLANK),
            noop(month_label(year, month)),
            button("▶️", CalCb(p=purpose, a="m", v=next_y * 100 + next_m).pack()) if has_next else noop(BLANK),
        ]
    )
    rows.append([noop(d) for d in WEEK_HEADER])

    first = to_gregorian(year, month, 1)
    # Column 0 is Saturday (Python weekday 5).
    lead = (first.weekday() - 5) % 7
    cells = [noop(BLANK) for _ in range(lead)]
    for day in range(1, days_in_month(year, month) + 1):
        g = first + timedelta(days=day - 1)
        label = to_persian_digits(day)
        if min_date <= g <= max_date:
            cells.append(button(label, CalCb(p=purpose, a="d", v=g.toordinal()).pack()))
        else:
            cells.append(noop("·"))
    while len(cells) % 7:
        cells.append(noop(BLANK))
    rows.extend(chunk(cells, 7))
    rows.append(cancel_row())
    return inline(rows)


def calendar_for(purpose: str, around: date, min_date: date, max_date: date) -> InlineKeyboardMarkup:
    anchor = min(max(around, min_date), max_date)
    j = to_jalali(anchor)
    return calendar(purpose, j.year, j.month, min_date, max_date)


def hour_picker(purpose: str, first_hour: int = 0, last_hour: int = 23) -> InlineKeyboardMarkup:
    hours = [
        button(to_persian_digits(f"{h:02d}"), TimeCb(p=purpose, a="h", h=h).pack())
        for h in range(first_hour, last_hour + 1)
    ]
    return inline([*chunk(hours, 6), cancel_row()])


def minute_picker(purpose: str, hour: int) -> InlineKeyboardMarkup:
    minutes = [
        button(to_persian_digits(f"{hour:02d}:{m:02d}"), TimeCb(p=purpose, a="m", h=hour, m=m).pack()) for m in MINUTES
    ]
    return inline([*chunk(minutes, 4), cancel_row()])
