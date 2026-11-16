from dataclasses import dataclass, field
from datetime import date, datetime
from uuid import UUID

from app.core.utils.jalali import SATURDAY, weekday_name
from app.domain.food.models import FoodGuestOrder, FoodMenu, FoodMenuItem, FoodOrder


@dataclass(frozen=True)
class DayMenu:
    day: date
    items: list[FoodMenuItem]
    deadline: datetime
    is_open: bool


@dataclass(frozen=True)
class MenuImportPreview:
    week_start: date
    week_end: date
    item_count: int
    day_count: int
    current_version: int  # 0 when the week has no menu yet
    affected_orders: int  # active orders whose dish is missing from the new menu
    price_changes: int  # dishes kept with a different price (existing orders keep their price)


@dataclass(frozen=True)
class ImportResult:
    menu: FoodMenu
    remapped_orders: int
    orphaned_orders: int
    # Orders whose dish is no longer on the new menu, on days still open: user → [(day, dish, guests or 0)]
    lost: dict[UUID, list[tuple[date, str, int]]] = field(default_factory=dict)


@dataclass(frozen=True)
class OrderResult:
    order: FoodOrder
    changed: bool  # False when the same choice was already registered


@dataclass(frozen=True)
class WeekOrderResult:
    """What «ثبت سفارش هفته» did, day by day."""

    saved: list[tuple[date, list[str]]] = field(default_factory=list)  # day, dish names
    cancelled: list[date] = field(default_factory=list)
    unchanged: list[date] = field(default_factory=list)
    skipped: list[tuple[date, str]] = field(default_factory=list)  # day, Persian reason

    @property
    def changed(self) -> bool:
        return bool(self.saved or self.cancelled)


@dataclass(frozen=True)
class GuestWeekResult:
    """What «ثبت سفارش مهمان» did, dish by dish."""

    saved: list[tuple[date, str, int]] = field(default_factory=list)  # day, dish, guests
    cancelled: list[tuple[date, str]] = field(default_factory=list)
    skipped: list[tuple[date, str, str]] = field(default_factory=list)  # day, dish, Persian reason

    @property
    def changed(self) -> bool:
        return bool(self.saved or self.cancelled)


@dataclass(frozen=True)
class InvoiceLine:
    day: date
    food_name: str
    is_guest: bool
    quantity: int
    unit_price: int
    total: int


@dataclass(frozen=True)
class Invoice:
    user_id: UUID
    full_name: str
    week_start: date
    week_end: date
    lines: list[InvoiceLine]
    employee_meals: int
    employee_amount: int
    guest_meals: int
    guest_amount: int

    @property
    def total(self) -> int:
        return self.employee_amount + self.guest_amount


@dataclass(frozen=True)
class MonthBill:
    """One person's food for a Jalali month (own meals only: there are no guests)."""

    user_id: UUID
    full_name: str
    year: int
    month: int
    start: date
    end: date
    lines: list[InvoiceLine]  # one per dish, by date

    @property
    def meals(self) -> int:
        return sum(line.quantity for line in self.lines)

    @property
    def total(self) -> int:
        return sum(line.total for line in self.lines)

    def by_weekday(self) -> list[tuple[str, list[str]]]:
        """«شنبه: ماکارونی، مرغ، …» — every dish of the month's Saturdays, then Sundays, … (from Saturday)."""
        groups: dict[int, list[InvoiceLine]] = {}
        for line in sorted(self.lines, key=lambda line: line.day):
            groups.setdefault(line.day.weekday(), []).append(line)
        order = sorted(groups, key=lambda wd: (wd - SATURDAY) % 7)
        return [
            (
                weekday_name(groups[wd][0].day),
                [line.food_name + (f" × {line.quantity}" if line.quantity > 1 else "") for line in groups[wd]],
            )
            for wd in order
        ]


@dataclass(frozen=True)
class DaySummary:
    day: date
    employee_counts: dict[str, int] = field(default_factory=dict)
    guest_counts: dict[str, int] = field(default_factory=dict)

    @property
    def employee_total(self) -> int:
        return sum(self.employee_counts.values())

    @property
    def guest_total(self) -> int:
        return sum(self.guest_counts.values())


@dataclass(frozen=True)
class FoodReportRow:
    day: date
    full_name: str
    food_name: str
    is_guest: bool
    guest_count: int
    unit_price: int
    total: int
    status: str
    created_at: datetime
    note: str


@dataclass(frozen=True)
class WeekOrders:
    orders: list[FoodOrder]
    guest_orders: list[FoodGuestOrder]
