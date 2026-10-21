import uuid
from datetime import date, datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import (
    AuthorshipMixin,
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    enum_column,
    utc_timestamp,
)


class MenuStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"


class OrderStatus(StrEnum):
    ACTIVE = "ACTIVE"
    CANCELLED = "CANCELLED"


ORDER_STATUS_FA = {OrderStatus.ACTIVE: "ثبت‌شده", OrderStatus.CANCELLED: "لغو شده"}


class FoodMenu(UUIDPrimaryKeyMixin, TimestampMixin, AuthorshipMixin, Base):
    """One uploaded version of a week's menu. Only one version per week is ACTIVE."""

    __tablename__ = "food_menus"
    __table_args__ = (sa.UniqueConstraint("week_start_date", "version"),)

    week_start_date: Mapped[date] = mapped_column(sa.Date, index=True)  # the Saturday
    version: Mapped[int] = mapped_column(sa.Integer)
    status: Mapped[MenuStatus] = mapped_column(enum_column(MenuStatus))
    source_filename: Mapped[str | None] = mapped_column(sa.String(200))
    stored_file_path: Mapped[str | None] = mapped_column(sa.String(300))
    item_count: Mapped[int] = mapped_column(sa.Integer, default=0)
    notified_at: Mapped[datetime | None] = mapped_column(utc_timestamp())

    items: Mapped[list["FoodMenuItem"]] = relationship(
        back_populates="menu", lazy="selectin", order_by="(FoodMenuItem.menu_date, FoodMenuItem.sort_order)"
    )


class FoodMenuItem(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "food_menu_items"
    __table_args__ = (sa.UniqueConstraint("menu_id", "menu_date", "food_name"),)

    menu_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, sa.ForeignKey("food_menus.id", ondelete="CASCADE"))
    menu_date: Mapped[date] = mapped_column(sa.Date, index=True)
    food_name: Mapped[str] = mapped_column(sa.String(120))
    price_toman: Mapped[int] = mapped_column(sa.Integer)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(sa.Integer, default=0)

    menu: Mapped[FoodMenu] = relationship(back_populates="items", lazy="joined")


class FoodOrder(UUIDPrimaryKeyMixin, TimestampMixin, AuthorshipMixin, Base):
    """An employee's own meal order for one date. Cancelling keeps the row (status)."""

    __tablename__ = "food_orders"
    __table_args__ = (sa.UniqueConstraint("user_id", "order_date"),)

    user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, sa.ForeignKey("users.id"), index=True)
    order_date: Mapped[date] = mapped_column(sa.Date, index=True)
    status: Mapped[OrderStatus] = mapped_column(enum_column(OrderStatus))
    note: Mapped[str | None] = mapped_column(sa.String(300))

    items: Mapped[list["FoodOrderItem"]] = relationship(
        back_populates="order", lazy="selectin", cascade="all, delete-orphan"
    )

    @property
    def total_price(self) -> int:
        return sum(item.unit_price_toman * item.quantity for item in self.items)


class FoodOrderItem(UUIDPrimaryKeyMixin, Base):
    """Name and price are copied at order time so menu edits never change past orders."""

    __tablename__ = "food_order_items"
    __table_args__ = (sa.UniqueConstraint("order_id", "menu_item_id"),)

    order_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, sa.ForeignKey("food_orders.id", ondelete="CASCADE"))
    menu_item_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, sa.ForeignKey("food_menu_items.id"))
    food_name: Mapped[str] = mapped_column(sa.String(120))
    unit_price_toman: Mapped[int] = mapped_column(sa.Integer)
    quantity: Mapped[int] = mapped_column(sa.Integer, default=1)

    order: Mapped[FoodOrder] = relationship(back_populates="items")


class FoodGuestOrder(UUIDPrimaryKeyMixin, TimestampMixin, AuthorshipMixin, Base):
    """Meals an employee orders for guests; billed to the employee but reported separately."""

    __tablename__ = "food_guest_orders"
    __table_args__ = (
        sa.UniqueConstraint("user_id", "order_date", "menu_item_id"),
        sa.CheckConstraint("guest_count > 0", name="positive_count"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, sa.ForeignKey("users.id"), index=True)
    order_date: Mapped[date] = mapped_column(sa.Date, index=True)
    menu_item_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, sa.ForeignKey("food_menu_items.id"))
    food_name: Mapped[str] = mapped_column(sa.String(120))
    unit_price_toman: Mapped[int] = mapped_column(sa.Integer)
    guest_count: Mapped[int] = mapped_column(sa.Integer)
    status: Mapped[OrderStatus] = mapped_column(enum_column(OrderStatus))
    note: Mapped[str | None] = mapped_column(sa.String(300))

    @property
    def total_price(self) -> int:
        return self.unit_price_toman * self.guest_count


class FoodWeeklySummary(UUIDPrimaryKeyMixin, Base):
    """Per-employee weekly invoice snapshot, refreshed whenever invoices are generated."""

    __tablename__ = "food_weekly_summaries"
    __table_args__ = (sa.UniqueConstraint("user_id", "week_start_date"),)

    user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, sa.ForeignKey("users.id"))
    week_start_date: Mapped[date] = mapped_column(sa.Date, index=True)
    employee_meal_count: Mapped[int] = mapped_column(sa.Integer, default=0)
    employee_amount_toman: Mapped[int] = mapped_column(sa.Integer, default=0)
    guest_meal_count: Mapped[int] = mapped_column(sa.Integer, default=0)
    guest_amount_toman: Mapped[int] = mapped_column(sa.Integer, default=0)
    total_amount_toman: Mapped[int] = mapped_column(sa.Integer, default=0)
    calculated_at: Mapped[datetime] = mapped_column(utc_timestamp())
