"""Which cells of the employee's Excel row a leave registered by an admin changes.

Times are minutes after midnight. The reference times come from the employee's own row of the day
(E «شروع شناوری» = 08:00, F «پایان شناوری» = 09:30, G «ساعت مجاز خروج» = 16:48 in the organization's
sheet), so the rules follow the person's file.

Hourly leave from x to y (exactly as the organization defined it; the three cases never overlap):

    1. start of the day   x <= F and y <  G   →  E = E + (y − x), F = y, J = y − x
    2. middle of the day  x >  F and y <  G   →  I = y − x
    3. end of the day     x >  F and y == G   →  G = x, J = y − x

Anything else (x >= y, x before E, y after G, or x <= F with y == G) is refused.
Daily leave: J = the whole official day (G − E).
"""

from dataclasses import dataclass

FLOAT_START = "شروع شناوری"  # E
FLOAT_END = "پایان شناوری"  # F
ALLOWED_EXIT = "ساعت مجاز خروج"  # G
MIDDAY_LEAVE = "مرخصی وسط روز"  # I
EDGE_LEAVE = "مرخصي اول و آخر"  # J (written with Arabic «ي» in the organization's file)

SCENARIO_TITLES = {
    1: "مرخصی اول روز",
    2: "مرخصی وسط روز",
    3: "مرخصی آخر روز",
    0: "مرخصی روزانه",
}


class LeaveRuleError(ValueError):
    """The leave fits none of the defined cases; the Persian reason is the message."""


@dataclass(frozen=True)
class DayTimes:
    """The reference times read from the employee's row (minutes after midnight)."""

    start: int  # E
    float_end: int  # F
    allowed_exit: int  # G


@dataclass(frozen=True)
class LeaveCells:
    scenario: int  # 1, 2, 3, or 0 for a daily leave
    cells: dict[str, int]  # column header -> minutes to write

    @property
    def title(self) -> str:
        return SCENARIO_TITLES[self.scenario]


def _clock(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def hourly_leave_cells(x: int, y: int, day: DayTimes) -> LeaveCells:
    """The cells an hourly leave from x to y changes. Raises LeaveRuleError."""
    if x >= y:
        raise LeaveRuleError("ساعت شروع مرخصی باید قبل از ساعت پایان آن باشد.")
    if x < day.start:
        raise LeaveRuleError(f"ساعت شروع مرخصی نمی‌تواند قبل از شروع ساعت کاری ({_clock(day.start)}) باشد.")
    if y > day.allowed_exit:
        raise LeaveRuleError(f"ساعت پایان مرخصی نمی‌تواند بعد از ساعت مجاز خروج ({_clock(day.allowed_exit)}) باشد.")
    duration = y - x
    if x <= day.float_end and y < day.allowed_exit:
        return LeaveCells(1, {FLOAT_START: day.start + duration, FLOAT_END: y, EDGE_LEAVE: duration})
    if x > day.float_end and y < day.allowed_exit:
        return LeaveCells(2, {MIDDAY_LEAVE: duration})
    if x > day.float_end and y == day.allowed_exit:
        return LeaveCells(3, {ALLOWED_EXIT: x, EDGE_LEAVE: duration})
    # x <= F and y == G: the whole working day as hourly leave
    raise LeaveRuleError(
        f"این بازه با هیچ‌یک از حالت‌های مرخصی ساعتی جور نیست (شروع تا {_clock(day.float_end)} و پایان "
        f"{_clock(day.allowed_exit)}). برای کل روز، «مرخصی روزانه» را انتخاب کنید."
    )


def daily_leave_cells(day: DayTimes) -> LeaveCells:
    """A daily leave: the whole official day in «مرخصي اول و آخر»."""
    if day.allowed_exit <= day.start:
        raise LeaveRuleError("ساعت‌های این روز در فایل اکسل درست نیست (ساعت مجاز خروج قبل از شروع).")
    return LeaveCells(0, {EDGE_LEAVE: day.allowed_exit - day.start})
