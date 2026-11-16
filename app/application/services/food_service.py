"""Weekly menus, employee and guest orders, deadlines, and food accounting."""

from collections import defaultdict
from datetime import date, datetime, timedelta
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.dto.food import (
    DayMenu,
    DaySummary,
    FoodReportRow,
    GuestWeekResult,
    ImportResult,
    Invoice,
    InvoiceLine,
    MenuImportPreview,
    MonthBill,
    OrderResult,
    WeekOrderResult,
)
from app.application.services import access
from app.core.config.policy import Policy
from app.core.exceptions import ConflictError, DeadlinePassedError, NotFoundError, ValidationError
from app.core.security.rbac import Permission
from app.core.utils.clock import TimeService
from app.core.utils.jalali import format_time, iter_days, month_range, to_jalali, week_range
from app.domain.audit.models import AuditAction
from app.domain.food.models import (
    ORDER_STATUS_FA,
    FoodGuestOrder,
    FoodMenu,
    FoodMenuItem,
    FoodOrder,
    FoodOrderItem,
    FoodWeeklySummary,
    MenuStatus,
    OrderStatus,
)
from app.domain.users.models import User
from app.infrastructure.excel.menu_parser import MenuParseResult
from app.infrastructure.repositories.audit import AuditRepository
from app.infrastructure.repositories.food import FoodRepository
from app.infrastructure.repositories.users import UserRepository


class FoodService:
    def __init__(self, session: AsyncSession, policy: Policy, time: TimeService):
        self.session = session
        self.policy = policy.food
        self.time = time
        self.repo = FoodRepository(session)
        self.users = UserRepository(session)
        self.audit = AuditRepository(session)

    # --- deadlines -------------------------------------------------------------------

    def deadline(self, day: date, guest: bool = False) -> datetime:
        at = self.policy.guest_deadline if guest else self.policy.ordering_deadline_time
        return self.time.to_local(self.time.local_to_utc(day, at))

    def is_open(self, day: date, guest: bool = False) -> bool:
        return self.time.now_local() < self.deadline(day, guest)

    def _require_open(self, day: date, guest: bool = False) -> None:
        if not self.is_open(day, guest):
            raise DeadlinePassedError(
                f"مهلت ثبت یا تغییر سفارش این روز ساعت {format_time(self.deadline(day, guest))} تمام شده است."
            )

    # --- menus -------------------------------------------------------------------------

    async def week_menu(self, day: date | None = None, guest: bool = False) -> list[DayMenu]:
        start, end = week_range(day or self.time.today())
        items = await self.repo.active_items_between(start, end)
        by_day: dict[date, list[FoodMenuItem]] = defaultdict(list)
        for item in items:
            by_day[item.menu_date].append(item)
        return [
            DayMenu(d, by_day[d], self.deadline(d, guest), self.is_open(d, guest))
            for d in iter_days(start, end)
            if by_day.get(d)
        ]

    async def upcoming_menu(self, guest: bool = False) -> list[DayMenu]:
        """Days of this week and next week that are still open for ordering."""
        today = self.time.today()
        days = await self.week_menu(today, guest) + await self.week_menu(today + timedelta(days=7), guest)
        return [d for d in days if d.is_open]

    async def day_menu(self, day: date, guest: bool = False) -> DayMenu | None:
        for entry in await self.week_menu(day, guest):
            if entry.day == day:
                return entry
        return None

    async def _orderable_item(self, item_id: UUID) -> FoodMenuItem:
        item = await self.repo.get_item(item_id)
        if item is None or not item.is_active or item.menu.status != MenuStatus.ACTIVE:
            raise NotFoundError("این غذا دیگر در منو نیست. لطفاً منو را دوباره باز کنید.")
        return item

    # --- employee orders ------------------------------------------------------------------

    async def get_order(self, user: User, day: date) -> FoodOrder | None:
        order = await self.repo.get_order(user.id, day)
        return order if order and order.status == OrderStatus.ACTIVE else None

    async def place_order(self, user: User, menu_item_id: UUID) -> OrderResult:
        access.require(user, Permission.FOOD_ORDER)
        item = await self._orderable_item(menu_item_id)
        day = item.menu_date
        self._require_open(day)

        order = await self.repo.get_order(user.id, day, for_update=True)
        if order is None:
            order = FoodOrder(user_id=user.id, order_date=day, status=OrderStatus.ACTIVE, created_by=user.id, items=[])
            try:
                async with self.session.begin_nested():
                    self.session.add(order)
            except IntegrityError:
                order = await self.repo.get_order(user.id, day, for_update=True)
                if order is None:
                    raise
        elif order.status == OrderStatus.ACTIVE:
            if any(i.menu_item_id == item.id for i in order.items):
                return OrderResult(order, changed=False)
            if not self.policy.allow_order_edit_before_deadline:
                raise ConflictError("برای این روز قبلاً سفارش ثبت کرده‌اید و تغییر آن مجاز نیست.")
        else:
            order.status = OrderStatus.ACTIVE
            order.items.clear()

        if len(order.items) >= self.policy.max_items_per_employee_order:
            if self.policy.max_items_per_employee_order == 1:
                order.items.clear()  # one meal per day: a new choice replaces the old one
                await self.session.flush()
            else:
                raise ConflictError("به حداکثر تعداد غذای مجاز برای این روز رسیده‌اید.")
        order.items.append(
            FoodOrderItem(menu_item_id=item.id, food_name=item.food_name, unit_price_toman=item.price_toman, quantity=1)
        )
        order.updated_by = user.id
        await self.session.flush()
        self.audit.record(
            AuditAction.FOOD_ORDER_PLACED,
            "food_order",
            order.id,
            user.id,
            {"date": day.isoformat(), "item": item.food_name, "price": item.price_toman},
        )
        await self.session.commit()
        await self.session.refresh(order, ["items"])
        return OrderResult(order, changed=True)

    async def cancel_order(self, user: User, day: date) -> FoodOrder:
        access.require(user, Permission.FOOD_ORDER)
        order = await self.repo.get_order(user.id, day, for_update=True)
        if order is None or order.status != OrderStatus.ACTIVE:
            raise NotFoundError("برای این روز سفارشی ثبت نشده است.")
        self._require_open(day)
        if not self.policy.allow_order_edit_before_deadline:
            raise ConflictError("لغو سفارش مجاز نیست.")
        order.status = OrderStatus.CANCELLED
        order.updated_by = user.id
        self.audit.record(AuditAction.FOOD_ORDER_CANCELLED, "food_order", order.id, user.id, {"date": day.isoformat()})
        await self.session.commit()
        return order

    # --- the whole week in one go ---------------------------------------------------------------

    async def current_choices(self, user: User, days: list[date]) -> dict[date, list[UUID]]:
        """The dishes the person has ordered on these days (active orders only)."""
        if not days:
            return {}
        orders = await self.repo.orders_between(min(days), max(days), user.id)
        return {o.order_date: [i.menu_item_id for i in o.items if i.menu_item_id] for o in orders if o.items}

    async def save_week(self, user: User, choices: dict[date, list[UUID]]) -> WeekOrderResult:
        """Make each listed day's order exactly the chosen dishes (none = cancel), in one transaction.

        Days whose deadline has passed, or that can't be changed, are skipped and reported, so one
        closed day never blocks the others. Every change is audited like a single order.
        """
        access.require(user, Permission.FOOD_ORDER)
        result = WeekOrderResult()
        for day in sorted(choices):
            wanted = list(dict.fromkeys(choices[day]))  # keep order, drop duplicates
            order = await self.repo.get_order(user.id, day, for_update=True)
            active = order is not None and order.status == OrderStatus.ACTIVE
            current = [i.menu_item_id for i in order.items] if active and order is not None else []
            if sorted(map(str, wanted)) == sorted(map(str, current)):
                result.unchanged.append(day)
                continue
            if not self.is_open(day):
                result.skipped.append((day, f"مهلت تا ساعت {format_time(self.deadline(day))} بود"))
                continue
            if active and not self.policy.allow_order_edit_before_deadline:
                result.skipped.append((day, "تغییر سفارش ثبت‌شده مجاز نیست"))
                continue
            if len(wanted) > self.policy.max_items_per_employee_order:
                result.skipped.append((day, "تعداد غذا بیشتر از حد مجاز است"))
                continue
            if not wanted:
                assert order is not None
                order.status = OrderStatus.CANCELLED
                order.updated_by = user.id
                self.audit.record(
                    AuditAction.FOOD_ORDER_CANCELLED, "food_order", order.id, user.id, {"date": day.isoformat()}
                )
                result.cancelled.append(day)
                continue
            items = []
            for item_id in wanted:
                item = await self._orderable_item(item_id)
                if item.menu_date != day:
                    raise ValidationError("انتخاب‌ها با منو جور نیست. لطفاً منو را دوباره باز کنید.")
                items.append(item)
            if order is None:
                order = FoodOrder(user_id=user.id, order_date=day, status=OrderStatus.ACTIVE, created_by=user.id)
                order.items = []
                try:
                    async with self.session.begin_nested():
                        self.session.add(order)
                except IntegrityError:
                    order = await self.repo.get_order(user.id, day, for_update=True)
                    if order is None:
                        raise
            order.status = OrderStatus.ACTIVE
            order.items.clear()
            await self.session.flush()
            for item in items:
                order.items.append(
                    FoodOrderItem(
                        menu_item_id=item.id, food_name=item.food_name, unit_price_toman=item.price_toman, quantity=1
                    )
                )
            order.updated_by = user.id
            await self.session.flush()
            self.audit.record(
                AuditAction.FOOD_ORDER_PLACED,
                "food_order",
                order.id,
                user.id,
                {"date": day.isoformat(), "items": [i.food_name for i in items], "via": "week"},
            )
            result.saved.append((day, [i.food_name for i in items]))
        await self.session.commit()
        return result

    # --- guest orders -----------------------------------------------------------------------

    def validate_guest_count(self, count: int) -> int:
        if count < 1 or count > self.policy.max_guest_count_per_order:
            raise ValidationError(f"تعداد مهمان باید بین ۱ تا {self.policy.max_guest_count_per_order} باشد.")
        return count

    async def place_guest_order(self, user: User, menu_item_id: UUID, guest_count: int) -> FoodGuestOrder:
        access.require(user, Permission.FOOD_ORDER)
        if not self.policy.allow_guest_orders:
            raise ValidationError("ثبت سفارش مهمان فعلاً امکان‌پذیر نیست.")
        self.validate_guest_count(guest_count)
        item = await self._orderable_item(menu_item_id)
        self._require_open(item.menu_date, guest=True)

        order = await self.repo.get_guest_order(user.id, item.menu_date, item.id, for_update=True)
        if order is None:
            order = FoodGuestOrder(
                user_id=user.id,
                order_date=item.menu_date,
                menu_item_id=item.id,
                food_name=item.food_name,
                unit_price_toman=item.price_toman,
                guest_count=guest_count,
                status=OrderStatus.ACTIVE,
                created_by=user.id,
            )
            try:
                async with self.session.begin_nested():
                    self.session.add(order)
            except IntegrityError:
                raise ConflictError("این سفارش مهمان همزمان ثبت شد. لطفاً گزارش را ببینید.") from None
        else:
            # Re-ordering the same dish for the same day replaces the guest count.
            order.guest_count = guest_count
            order.status = OrderStatus.ACTIVE
            order.updated_by = user.id
        await self.session.flush()
        self.audit.record(
            AuditAction.FOOD_GUEST_ORDER_PLACED,
            "food_guest_order",
            order.id,
            user.id,
            {"date": item.menu_date.isoformat(), "item": item.food_name, "count": guest_count},
        )
        await self.session.commit()
        return order

    async def current_guest_counts(self, user: User, days: list[date]) -> dict[UUID, int]:
        """Guests per menu dish the person has ordered on these days (active orders only)."""
        if not days:
            return {}
        orders = await self.repo.guest_orders_between(min(days), max(days), user.id)
        return {o.menu_item_id: o.guest_count for o in orders}

    async def save_guest_week(self, user: User, counts: dict[UUID, int]) -> GuestWeekResult:
        """Make each dish's guest count exactly the given number (0 = cancel), in one transaction.

        Dishes whose day has closed, or with a count over the limit, are skipped and reported.
        """
        access.require(user, Permission.FOOD_ORDER)
        if not self.policy.allow_guest_orders:
            raise ValidationError("ثبت سفارش مهمان فعلاً امکان‌پذیر نیست.")
        result = GuestWeekResult()
        items = {}
        for item_id in counts:
            item = await self.repo.get_item(item_id)
            if item is not None:
                items[item_id] = item
        for item in sorted(items.values(), key=lambda i: (i.menu_date, i.food_name)):
            item_id, count = item.id, counts[item.id]
            day = item.menu_date
            order = await self.repo.get_guest_order(user.id, day, item.id, for_update=True)
            current = order.guest_count if order is not None and order.status == OrderStatus.ACTIVE else 0
            if count == current:
                continue
            if not self.is_open(day, guest=True):
                reason = f"مهلت تا ساعت {format_time(self.deadline(day, guest=True))} بود"
                result.skipped.append((day, item.food_name, reason))
                continue
            if count < 0 or count > self.policy.max_guest_count_per_order:
                result.skipped.append((day, item.food_name, f"حداکثر {self.policy.max_guest_count_per_order} نفر"))
                continue
            if count == 0:
                assert order is not None
                order.status = OrderStatus.CANCELLED
                order.updated_by = user.id
                self.audit.record(AuditAction.FOOD_GUEST_ORDER_CANCELLED, "food_guest_order", order.id, user.id, {})
                result.cancelled.append((day, item.food_name))
                continue
            item = await self._orderable_item(item_id)
            if order is None:
                order = FoodGuestOrder(
                    user_id=user.id,
                    order_date=day,
                    menu_item_id=item.id,
                    food_name=item.food_name,
                    unit_price_toman=item.price_toman,
                    guest_count=count,
                    status=OrderStatus.ACTIVE,
                    created_by=user.id,
                )
                try:
                    async with self.session.begin_nested():
                        self.session.add(order)
                except IntegrityError:
                    raise ConflictError("سفارش مهمان همزمان ثبت شد. لطفاً دوباره امتحان کنید.") from None
            else:
                order.guest_count = count
                order.status = OrderStatus.ACTIVE
                order.updated_by = user.id
            await self.session.flush()
            self.audit.record(
                AuditAction.FOOD_GUEST_ORDER_PLACED,
                "food_guest_order",
                order.id,
                user.id,
                {"date": day.isoformat(), "item": item.food_name, "count": count, "via": "week"},
            )
            result.saved.append((day, item.food_name, count))
        await self.session.commit()
        return result

    async def cancel_guest_order(self, user: User, guest_order_id: UUID) -> FoodGuestOrder:
        access.require(user, Permission.FOOD_ORDER)
        order = await self.repo.get_guest_order_by_id(guest_order_id)
        if order is None or order.user_id != user.id or order.status != OrderStatus.ACTIVE:
            raise NotFoundError("سفارش مهمان پیدا نشد.")
        self._require_open(order.order_date, guest=True)
        order.status = OrderStatus.CANCELLED
        order.updated_by = user.id
        self.audit.record(AuditAction.FOOD_GUEST_ORDER_CANCELLED, "food_guest_order", order.id, user.id, {})
        await self.session.commit()
        return order

    # --- employee invoice ---------------------------------------------------------------------

    async def invoice(self, user: User, day: date | None = None) -> Invoice:
        access.require(user, Permission.FOOD_ORDER)
        start, end = week_range(day or self.time.today())
        orders = await self.repo.orders_between(start, end, user.id)
        guests = await self.repo.guest_orders_between(start, end, user.id)
        return self._build_invoice(user, start, end, orders, guests)

    def _build_invoice(
        self, user: User, start: date, end: date, orders: list[FoodOrder], guests: list[FoodGuestOrder]
    ) -> Invoice:
        lines: list[InvoiceLine] = []
        for order in orders:
            for item in order.items:
                lines.append(
                    InvoiceLine(
                        order.order_date,
                        item.food_name,
                        False,
                        item.quantity,
                        item.unit_price_toman,
                        item.quantity * item.unit_price_toman,
                    )
                )
        for g in guests:
            lines.append(InvoiceLine(g.order_date, g.food_name, True, g.guest_count, g.unit_price_toman, g.total_price))
        lines.sort(key=lambda line: (line.day, line.is_guest))
        own = [line for line in lines if not line.is_guest]
        guest_lines = [line for line in lines if line.is_guest]
        return Invoice(
            user_id=user.id,
            full_name=user.full_name,
            week_start=start,
            week_end=end,
            lines=lines,
            employee_meals=sum(line.quantity for line in own),
            employee_amount=sum(line.total for line in own),
            guest_meals=sum(line.quantity for line in guest_lines),
            guest_amount=sum(line.total for line in guest_lines),
        )

    # --- monthly bill ---------------------------------------------------------------------------

    def _month_bill(self, user: User, start: date, end: date, orders: list[FoodOrder]) -> MonthBill:
        j = to_jalali(start)
        lines = [
            InvoiceLine(
                o.order_date, i.food_name, False, i.quantity, i.unit_price_toman, i.quantity * i.unit_price_toman
            )
            for o in orders
            for i in o.items
        ]
        lines.sort(key=lambda line: line.day)
        return MonthBill(user.id, user.full_name, j.year, j.month, start, end, lines)

    async def month_bill(self, user: User, day: date | None = None) -> MonthBill:
        """The person's own bill for the Jalali month of `day` (default: this month)."""
        access.require(user, Permission.FOOD_ORDER)
        start, end = month_range(day or self.time.today())
        return self._month_bill(user, start, end, await self.repo.orders_between(start, end, user.id))

    async def team_month_bills(self, actor: User, day: date) -> list[MonthBill]:
        """Everyone who ordered in that Jalali month, by name."""
        access.require(actor, Permission.FOOD_REPORT_ALL)
        start, end = month_range(day)
        orders = await self.repo.orders_between(start, end)
        users = await self.users.get_many({o.user_id for o in orders})
        bills = [
            self._month_bill(u, start, end, [o for o in orders if o.user_id == u.id])
            for u in sorted(users.values(), key=lambda u: u.full_name)
        ]
        return [b for b in bills if b.lines]

    # --- admin: menu import ---------------------------------------------------------------------

    async def preview_import(self, actor: User, parsed: MenuParseResult) -> MenuImportPreview:
        access.require(actor, Permission.FOOD_MENU_MANAGE)
        if not parsed.ok or parsed.week_start is None:
            raise ValidationError("فایل منو خطا دارد.")
        current = await self.repo.active_menu(parsed.week_start)
        affected = price_changes = 0
        if current is not None:
            new_keys = {(r.menu_date, r.food_name): r for r in parsed.rows if r.is_active}
            old_items = {i.id: i for i in current.items}
            order_items = await self.repo.order_items_for_menu_items(list(old_items))
            guest_orders = await self.repo.guest_orders_for_menu_items(list(old_items))
            for menu_item_id in [oi.menu_item_id for oi in order_items] + [g.menu_item_id for g in guest_orders]:
                old = old_items[menu_item_id]
                if (old.menu_date, old.food_name) not in new_keys:
                    affected += 1
            for old in current.items:
                new = new_keys.get((old.menu_date, old.food_name))
                if new and new.price_toman != old.price_toman:
                    price_changes += 1
        return MenuImportPreview(
            week_start=parsed.week_start,
            week_end=parsed.week_start + timedelta(days=6),
            item_count=len(parsed.rows),
            day_count=len({r.menu_date for r in parsed.rows}),
            current_version=current.version if current else 0,
            affected_orders=affected,
            price_changes=price_changes,
        )

    async def import_menu(
        self, actor: User, parsed: MenuParseResult, filename: str | None, stored_path: str | None
    ) -> ImportResult:
        access.require(actor, Permission.FOOD_MENU_MANAGE)
        if not parsed.ok or parsed.week_start is None:
            raise ValidationError("فایل منو خطا دارد.")
        current = await self.repo.active_menu(parsed.week_start, for_update=True)
        version = await self.repo.latest_version(parsed.week_start) + 1
        if current is not None:
            current.status = MenuStatus.SUPERSEDED
            current.updated_by = actor.id
            await self.session.flush()
        menu = FoodMenu(
            week_start_date=parsed.week_start,
            version=version,
            status=MenuStatus.ACTIVE,
            source_filename=(filename or "")[:200] or None,
            stored_file_path=stored_path,
            item_count=len(parsed.rows),
            created_by=actor.id,
            items=[],
        )
        sort_counter: dict[date, int] = defaultdict(int)
        for row in sorted(parsed.rows, key=lambda r: (r.menu_date, r.row_number)):
            menu.items.append(
                FoodMenuItem(
                    menu_date=row.menu_date,
                    food_name=row.food_name,
                    price_toman=row.price_toman,
                    is_active=row.is_active,
                    sort_order=sort_counter[row.menu_date],
                )
            )
            sort_counter[row.menu_date] += 1
        self.session.add(menu)
        await self.session.flush()

        remapped = orphaned = 0
        lost: dict[UUID, list[tuple[date, str, int]]] = defaultdict(list)
        if current is not None:
            # Keep existing orders attached to the same dish in the new version; the
            # ordered name and price are snapshots and never change.
            new_by_key = {(i.menu_date, i.food_name): i for i in menu.items if i.is_active}
            old_items = {i.id: i for i in current.items}
            for oi in await self.repo.order_items_for_menu_items(list(old_items)):
                target = new_by_key.get((old_items[oi.menu_item_id].menu_date, old_items[oi.menu_item_id].food_name))
                if target:
                    oi.menu_item_id = target.id
                    remapped += 1
                else:
                    orphaned += 1
                    if self.is_open(oi.order.order_date):
                        lost[oi.order.user_id].append((oi.order.order_date, oi.food_name, 0))
            for g in await self.repo.guest_orders_for_menu_items(list(old_items)):
                target = new_by_key.get((old_items[g.menu_item_id].menu_date, old_items[g.menu_item_id].food_name))
                if target:
                    g.menu_item_id = target.id
                    remapped += 1
                else:
                    orphaned += 1
                    if self.is_open(g.order_date, guest=True):
                        lost[g.user_id].append((g.order_date, g.food_name, g.guest_count))
        self.audit.record(
            AuditAction.FOOD_MENU_IMPORTED,
            "food_menu",
            menu.id,
            actor.id,
            {
                "week_start": parsed.week_start.isoformat(),
                "version": version,
                "items": len(parsed.rows),
                "filename": filename,
                "remapped_orders": remapped,
                "orphaned_orders": orphaned,
            },
        )
        await self.session.commit()
        return ImportResult(menu, remapped, orphaned, {u: sorted(v) for u, v in lost.items()})

    async def latest_menu(self) -> FoodMenu | None:
        return await self.repo.latest_active_menu()

    async def broadcast_recipients(self, actor: User) -> list[User]:
        """Everyone active (employees, food admins and other staff)."""
        access.require(actor, Permission.FOOD_BROADCAST)
        return await self.users.list_active()

    async def record_announcement(
        self, actor: User, announcement_id: UUID, menu_id: UUID | None, text: str, result: dict[str, int]
    ) -> None:
        """An «📣 اطلاع‌رسانی» was sent. It also marks the latest menu as announced, after which order
        changes are reported to the food admins."""
        if menu_id is not None:
            menu = await self.repo.get(menu_id)
            if menu is not None and menu.notified_at is None:
                menu.notified_at = self.time.now_utc()
        self.audit.record(
            AuditAction.BULK_NOTIFICATION,
            "food_announcement",
            announcement_id,
            actor.id,
            {**result, "menu_id": str(menu_id) if menu_id else None, "text": text[:300]},
        )
        await self.session.commit()

    async def announced(self, days: list[date]) -> bool:
        """Was the menu of any of these days' weeks already announced?"""
        for start in {week_range(d)[0] for d in days}:
            menu = await self.repo.active_menu(start)
            if menu is not None and menu.notified_at is not None:
                return True
        return False

    async def mark_notified(self, menu_id: UUID, actor: User, result: dict[str, int]) -> None:
        menu = await self.repo.get(menu_id)
        if menu is not None:
            menu.notified_at = self.time.now_utc()
        self.audit.record(AuditAction.BULK_NOTIFICATION, "food_menu", menu_id, actor.id, result)
        await self.session.commit()

    # --- admin: reports ---------------------------------------------------------------------------

    async def day_summaries(self, actor: User, start: date, end: date) -> list[DaySummary]:
        access.require(actor, Permission.FOOD_REPORT_ALL)
        orders = await self.repo.orders_between(start, end)
        guests = await self.repo.guest_orders_between(start, end)
        summaries: dict[date, DaySummary] = {}
        for order in orders:
            summary = summaries.setdefault(order.order_date, DaySummary(order.order_date, {}, {}))
            for item in order.items:
                summary.employee_counts[item.food_name] = summary.employee_counts.get(item.food_name, 0) + item.quantity
        for g in guests:
            summary = summaries.setdefault(g.order_date, DaySummary(g.order_date, {}, {}))
            summary.guest_counts[g.food_name] = summary.guest_counts.get(g.food_name, 0) + g.guest_count
        return [summaries[d] for d in sorted(summaries)]

    async def detailed_rows(self, actor: User, start: date, end: date) -> list[FoodReportRow]:
        access.require(actor, Permission.FOOD_REPORT_ALL)
        orders = await self.repo.orders_between(start, end, include_cancelled=True)
        guests = await self.repo.guest_orders_between(start, end, include_cancelled=True)
        users = await self.users.get_many({o.user_id for o in orders} | {g.user_id for g in guests})
        rows: list[FoodReportRow] = []
        for order in orders:
            name = users[order.user_id].full_name if order.user_id in users else "?"
            for item in order.items:
                rows.append(
                    FoodReportRow(
                        day=order.order_date,
                        full_name=name,
                        food_name=item.food_name,
                        is_guest=False,
                        guest_count=0,
                        unit_price=item.unit_price_toman,
                        total=item.unit_price_toman * item.quantity,
                        status=ORDER_STATUS_FA[order.status],
                        created_at=self.time.to_local(order.created_at),
                        note=order.note or "",
                    )
                )
        for g in guests:
            rows.append(
                FoodReportRow(
                    day=g.order_date,
                    full_name=users[g.user_id].full_name if g.user_id in users else "?",
                    food_name=g.food_name,
                    is_guest=True,
                    guest_count=g.guest_count,
                    unit_price=g.unit_price_toman,
                    total=g.total_price,
                    status=ORDER_STATUS_FA[g.status],
                    created_at=self.time.to_local(g.created_at),
                    note=g.note or "",
                )
            )
        rows.sort(key=lambda r: (r.day, r.full_name, r.is_guest))
        self.audit.record(
            AuditAction.REPORT_GENERATED,
            "food_report",
            None,
            actor.id,
            {"start": start.isoformat(), "end": end.isoformat()},
        )
        await self.session.commit()
        return rows

    async def team_invoices(self, actor: User, day: date | None = None) -> list[Invoice]:
        """Weekly invoice for every employee with orders; also refreshes food_weekly_summaries."""
        access.require(actor, Permission.FOOD_REPORT_ALL)
        start, end = week_range(day or self.time.today())
        orders = await self.repo.orders_between(start, end)
        guests = await self.repo.guest_orders_between(start, end)
        users = await self.users.get_many({o.user_id for o in orders} | {g.user_id for g in guests})
        invoices: list[Invoice] = []
        now = self.time.now_utc()
        for user in sorted(users.values(), key=lambda u: u.full_name):
            invoice = self._build_invoice(
                user,
                start,
                end,
                [o for o in orders if o.user_id == user.id],
                [g for g in guests if g.user_id == user.id],
            )
            invoices.append(invoice)
            summary = await self.repo.get_weekly_summary(user.id, start)
            if summary is None:
                summary = FoodWeeklySummary(user_id=user.id, week_start_date=start, calculated_at=now)
                self.session.add(summary)
            summary.employee_meal_count = invoice.employee_meals
            summary.employee_amount_toman = invoice.employee_amount
            summary.guest_meal_count = invoice.guest_meals
            summary.guest_amount_toman = invoice.guest_amount
            summary.total_amount_toman = invoice.total
            summary.calculated_at = now
        self.audit.record(
            AuditAction.REPORT_GENERATED, "food_invoices", None, actor.id, {"week_start": start.isoformat()}
        )
        await self.session.commit()
        return invoices
