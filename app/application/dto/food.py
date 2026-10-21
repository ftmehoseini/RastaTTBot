from dataclasses import dataclass, field
from datetime import date, datetime
from uuid import UUID

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


@dataclass(frozen=True)
class OrderResult:
    order: FoodOrder
    changed: bool  # False when the same choice was already registered


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
