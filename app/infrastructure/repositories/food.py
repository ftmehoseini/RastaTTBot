from datetime import date
from uuid import UUID

from sqlalchemy import func, select

from app.domain.food.models import (
    FoodGuestOrder,
    FoodMenu,
    FoodMenuItem,
    FoodOrder,
    FoodOrderItem,
    FoodWeeklySummary,
    MenuStatus,
    OrderStatus,
)
from app.infrastructure.repositories.base import Repository


class FoodRepository(Repository[FoodMenu]):
    model = FoodMenu

    # --- menus ---------------------------------------------------------------

    async def active_menu(self, week_start: date, *, for_update: bool = False) -> FoodMenu | None:
        stmt = select(FoodMenu).where(FoodMenu.week_start_date == week_start, FoodMenu.status == MenuStatus.ACTIVE)
        if for_update:
            stmt = stmt.with_for_update()
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def latest_version(self, week_start: date) -> int:
        stmt = select(func.max(FoodMenu.version)).where(FoodMenu.week_start_date == week_start)
        return (await self.session.execute(stmt)).scalar_one() or 0

    async def latest_active_menu(self) -> FoodMenu | None:
        stmt = (
            select(FoodMenu)
            .where(FoodMenu.status == MenuStatus.ACTIVE)
            .order_by(FoodMenu.week_start_date.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def active_items_between(self, start: date, end: date) -> list[FoodMenuItem]:
        stmt = (
            select(FoodMenuItem)
            .join(FoodMenu, FoodMenu.id == FoodMenuItem.menu_id)
            .where(
                FoodMenu.status == MenuStatus.ACTIVE,
                FoodMenuItem.is_active.is_(True),
                FoodMenuItem.menu_date >= start,
                FoodMenuItem.menu_date <= end,
            )
            .order_by(FoodMenuItem.menu_date, FoodMenuItem.sort_order)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def get_item(self, item_id: UUID) -> FoodMenuItem | None:
        return await self.session.get(FoodMenuItem, item_id)

    # --- employee orders -----------------------------------------------------

    async def get_order(self, user_id: UUID, order_date: date, *, for_update: bool = False) -> FoodOrder | None:
        stmt = select(FoodOrder).where(FoodOrder.user_id == user_id, FoodOrder.order_date == order_date)
        if for_update:
            stmt = stmt.with_for_update()
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def orders_between(
        self, start: date, end: date, user_id: UUID | None = None, include_cancelled: bool = False
    ) -> list[FoodOrder]:
        stmt = select(FoodOrder).where(FoodOrder.order_date >= start, FoodOrder.order_date <= end)
        if not include_cancelled:
            stmt = stmt.where(FoodOrder.status == OrderStatus.ACTIVE)
        if user_id is not None:
            stmt = stmt.where(FoodOrder.user_id == user_id)
        stmt = stmt.order_by(FoodOrder.order_date, FoodOrder.created_at)
        return list((await self.session.execute(stmt)).scalars())

    async def order_items_for_menu_items(self, item_ids: list[UUID]) -> list[FoodOrderItem]:
        if not item_ids:
            return []
        stmt = (
            select(FoodOrderItem)
            .join(FoodOrder, FoodOrder.id == FoodOrderItem.order_id)
            .where(FoodOrderItem.menu_item_id.in_(item_ids), FoodOrder.status == OrderStatus.ACTIVE)
        )
        return list((await self.session.execute(stmt)).scalars())

    # --- guest orders ----------------------------------------------------------

    async def get_guest_order(
        self, user_id: UUID, order_date: date, menu_item_id: UUID, *, for_update: bool = False
    ) -> FoodGuestOrder | None:
        stmt = select(FoodGuestOrder).where(
            FoodGuestOrder.user_id == user_id,
            FoodGuestOrder.order_date == order_date,
            FoodGuestOrder.menu_item_id == menu_item_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_guest_order_by_id(self, guest_order_id: UUID) -> FoodGuestOrder | None:
        return await self.session.get(FoodGuestOrder, guest_order_id, with_for_update=True)

    async def guest_orders_between(
        self, start: date, end: date, user_id: UUID | None = None, include_cancelled: bool = False
    ) -> list[FoodGuestOrder]:
        stmt = select(FoodGuestOrder).where(FoodGuestOrder.order_date >= start, FoodGuestOrder.order_date <= end)
        if not include_cancelled:
            stmt = stmt.where(FoodGuestOrder.status == OrderStatus.ACTIVE)
        if user_id is not None:
            stmt = stmt.where(FoodGuestOrder.user_id == user_id)
        stmt = stmt.order_by(FoodGuestOrder.order_date, FoodGuestOrder.created_at)
        return list((await self.session.execute(stmt)).scalars())

    async def guest_orders_for_menu_items(self, item_ids: list[UUID]) -> list[FoodGuestOrder]:
        if not item_ids:
            return []
        stmt = select(FoodGuestOrder).where(
            FoodGuestOrder.menu_item_id.in_(item_ids), FoodGuestOrder.status == OrderStatus.ACTIVE
        )
        return list((await self.session.execute(stmt)).scalars())

    # --- summaries -------------------------------------------------------------

    async def get_weekly_summary(self, user_id: UUID, week_start: date) -> FoodWeeklySummary | None:
        stmt = select(FoodWeeklySummary).where(
            FoodWeeklySummary.user_id == user_id, FoodWeeklySummary.week_start_date == week_start
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()
