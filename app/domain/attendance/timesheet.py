"""The organization's timesheet formulas, minute for minute the same as its Excel workbook.

Column letters refer to the timesheet table (see infrastructure/excel/timesheet.py, which writes
the same formulas into Excel). Times are minutes after midnight of the work date; an empty cell is
0, exactly as in Excel, and a check-out after midnight is simply larger than 1440.

    D ساعت ورود         E شروع شناوری      F پایان شناوری      G ساعت مجاز خروج
    H ساعت خروج         I مرخصی با حقوق
    J ساعت مفید          = H - D
    K کارکرد عادی روزانه = presence inside E..G (from D, or from E when D is empty or earlier)
    L تاخیر مجاز          = D - E inside the floating window, F - E after it
    M تاخیر غیر مجاز      = D - F after the floating window
    N تعجیل               = G - H when H is before G
    O اضافه کار روزانه    = MAX(J - K, 0): presence before E and after G
"""

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class SheetRules:
    floating_start: int  # E, e.g. 08:00 = 480
    floating_end: int  # F, e.g. 09:30 = 570
    allowed_exit: int  # G, e.g. 16:48 = 1008
    late_cap: int  # L of the summary: compensable unauthorized lateness per month (420 = 7 h)
    early_cap: int  # compensable early departure per month (180 = 3 h)
    surplus_factor: float = 0.8  # کارکرد نهایی: + overtime surplus × this
    penalty_factor: float = 1.6  # کارکرد نهایی: − uncompensated lateness/early departure × this


@dataclass(frozen=True)
class SheetDay:
    useful: int  # J ساعت مفید
    normal: int  # K کارکرد عادی روزانه
    floating_late: int  # L تاخیر مجاز
    late: int  # M تاخیر غیر مجاز
    early: int  # N تعجیل
    overtime: int  # O اضافه کار روزانه
    paid_leave: int = 0  # I مرخصی با حقوق


def sheet_day(d: int | None, h: int | None, e: int, f: int, g: int, paid_leave: int = 0) -> SheetDay:
    """One table row. `d`/`h` are None for an empty cell (Excel treats it as 0)."""
    d = d or 0
    h = h or 0
    j = h - d
    # K counts from the check-in, or from E when there is none or it was earlier than E.
    start = d if d > 0 and d >= e else e
    k = max(h - start, 0) if h <= g else g - start
    if e <= d <= f:
        l = d - e  # noqa: E741 - the Excel column letter
    elif d > f:
        l = f - e  # noqa: E741
    else:
        l = 0  # noqa: E741
    m = d - f if d > f else 0
    n = max(g - h, 0) if h > 0 else 0
    o = max(j - k, 0)
    return SheetDay(useful=j, normal=k, floating_late=l, late=m, early=n, overtime=o, paid_leave=paid_leave)


@dataclass(frozen=True)
class SheetMonth:
    normal: int  # مجموع کارکرد عادی = SUM(K)
    overtime: int  # مجموع اضافه کار = SUM(O)
    floating_late: int  # مجموع تاخیر مجاز = SUM(L)
    late: int  # مجموع تاخیر غیرمجاز = SUM(M)
    early: int  # مجموع تعجیل = SUM(N)
    paid_leave: int  # مرخصی مصرفی = SUM(I)
    late_cap: int
    early_cap: int
    surplus_factor: float = 0.8
    penalty_factor: float = 1.6

    @property
    def late_over_cap(self) -> int:
        """تاخیر غیرمجاز غیرقابل جبران = MAX(K34-L34, 0)"""
        return max(self.late - self.late_cap, 0)

    @property
    def early_over_cap(self) -> int:
        """تعجیل غیرقابل جبران = MAX(K35-L35, 0)"""
        return max(self.early - self.early_cap, 0)

    @property
    def compensable(self) -> int:
        """مقدار تاخیر و تعجیل قابل جبران = K33 + MIN(K34, L34) + MIN(K35, L35)"""
        return self.floating_late + min(self.late, self.late_cap) + min(self.early, self.early_cap)

    @property
    def non_compensable(self) -> int:
        """Lateness and early departure past the monthly caps."""
        return self.late_over_cap + self.early_over_cap

    @property
    def overtime_surplus(self) -> int:
        """مقدار اضافه کار مازاد بر نیاز = MAX(K32 - P32, 0)"""
        return max(self.overtime - self.compensable, 0)

    @property
    def uncompensated(self) -> int:
        """میزان تاخیر و تعجیل جبران نشده = MAX(P32-K32, 0) + MAX(K34-L34, 0) + MAX(K35-L35, 0)"""
        return max(self.compensable - self.overtime, 0) + self.late_over_cap + self.early_over_cap

    @property
    def final_work(self) -> int:
        """کارکرد نهایی = B32 + MIN(K32, P32) + B34 + P33 × 0.8 − P34 × 1.6 (factors from the policy)."""
        value = (
            self.normal
            + min(self.overtime, self.compensable)
            + self.paid_leave
            + self.overtime_surplus * self.surplus_factor
            - self.uncompensated * self.penalty_factor
        )
        return round(value)


def sheet_month(days: Iterable[SheetDay], rules: SheetRules) -> SheetMonth:
    days = list(days)
    return SheetMonth(
        normal=sum(x.normal for x in days),
        overtime=sum(x.overtime for x in days),
        floating_late=sum(x.floating_late for x in days),
        late=sum(x.late for x in days),
        early=sum(x.early for x in days),
        paid_leave=sum(x.paid_leave for x in days),
        late_cap=rules.late_cap,
        early_cap=rules.early_cap,
        surplus_factor=rules.surplus_factor,
        penalty_factor=rules.penalty_factor,
    )
