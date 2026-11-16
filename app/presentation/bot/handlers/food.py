"""Food ordering UI for employees. Rules (deadline, one meal per day, guests) live in FoodService."""

from datetime import date, timedelta
from uuid import UUID

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.application.presenters import food as texts
from app.application.services import access
from app.application.services.container import Services
from app.application.use_cases.food_broadcast import OPEN_FOOD_MENU
from app.core.exceptions import ExpiredActionError, NotFoundError
from app.core.security.rbac import Permission
from app.core.utils.jalali import format_date, format_date_short
from app.core.utils.text import format_money, format_number, parse_int, to_persian_digits
from app.domain.users.models import User
from app.presentation.bot.callbacks.data import OPEN_FOOD, FoodAdminCb, FoodCb, NavCb
from app.presentation.bot.handlers.common import show
from app.presentation.bot.keyboards.common import back_row, button, cancel_row, chunk, confirm_rows, inline
from app.presentation.bot.middlewares.filters import IsRegistered
from app.presentation.bot.modules import BTN_FOOD
from app.presentation.bot.states.flows import GuestFlow

router = Router(name="food")
router.message.filter(IsRegistered())
router.callback_query.filter(IsRegistered())

MENU_TEXT = "🍽 سفارش غذا\nیکی از گزینه‌ها را انتخاب کنید:"


def menu_markup(user: User):
    rows = [
        [button("📋 منو و سفارش هفته", FoodCb(a="menu").pack()), button("🍛 سفارش امروز", FoodCb(a="today").pack())],
        [button("👥 سفارش مهمان", FoodCb(a="guest").pack()), button("🧾 صورت‌حساب من", FoodCb(a="invoice").pack())],
    ]
    if access.can(user, Permission.FOOD_MENU_MANAGE) or access.can(user, Permission.FOOD_REPORT_ALL):
        rows.append([button("⚙️ مدیریت غذا", FoodAdminCb(a="menu").pack())])
    rows.append(back_row())
    return inline(rows)


def _back():
    return back_row("food")


@router.message(F.text == BTN_FOOD)
async def open_menu(message: Message, state: FSMContext, user: User) -> None:
    await state.clear()
    await message.answer(MENU_TEXT, reply_markup=menu_markup(user))


@router.callback_query(F.data == OPEN_FOOD)
@router.callback_query(NavCb.filter(F.to == "food"))
async def open_menu_button(query: CallbackQuery, state: FSMContext, user: User) -> None:
    await state.clear()
    await show(query, MENU_TEXT, menu_markup(user))


# --- menu and own order -------------------------------------------------------------------------


# --- the whole week on one screen: every dish with its price, choose, confirm once ----------------------

WEEK_KEY = "food_week"  # FSM data: {"days": [ordinals], "chosen": {ordinal: [item hex]}}


def _week_markup(days, chosen: dict[date, list[UUID]]):
    rows = []
    for day in days:
        picked = set(chosen.get(day.day, []))
        for item in day.items:
            mark = "✅" if item.id in picked else "▫️"
            label = f"{mark} {format_date_short(day.day)} · {item.food_name} · {format_number(item.price_toman)}"
            rows.append([button(label, FoodCb(a="w", v=item.id.hex).pack())])
    count = sum(1 for d in days if chosen.get(d.day))
    rows.append([button(f"✅ ثبت سفارش هفته ({to_persian_digits(count)} روز)", FoodCb(a="w_ok").pack())])
    rows.append([button("↩️ برگرداندن به سفارش‌های ثبت‌شده", FoodCb(a="w_reset").pack())])
    rows.append(_back())
    return inline(rows)


async def _week_state(state: FSMContext, services: Services, user: User, fresh: bool = False):
    """The open days and the person's current choices (kept between taps until confirmed)."""
    days = await services.food.upcoming_menu()
    stored = (await state.get_data()).get(WEEK_KEY)
    ordinals = [d.day.toordinal() for d in days]
    if fresh or not stored or stored.get("days") != ordinals:
        current = await services.food.current_choices(user, [d.day for d in days])
        chosen = {d.day: current.get(d.day, []) for d in days}
    else:
        chosen = {date.fromordinal(int(k)): [UUID(hex=h) for h in v] for k, v in stored["chosen"].items()}
    return days, chosen


async def _save_week_state(state: FSMContext, days, chosen: dict[date, list[UUID]]) -> None:
    await state.update_data(
        {
            WEEK_KEY: {
                "days": [d.day.toordinal() for d in days],
                "chosen": {str(k.toordinal()): [u.hex for u in v] for k, v in chosen.items()},
            }
        }
    )


async def _show_week(query: CallbackQuery, state: FSMContext, services: Services, user: User, fresh: bool) -> None:
    days, chosen = await _week_state(state, services, user, fresh=fresh)
    await _save_week_state(state, days, chosen)
    if not days:
        await show(query, texts.week_order(days, chosen, services.policy.food), inline([_back()]))
        return
    markup = _week_markup(days, chosen)
    await show(query, texts.week_order(days, chosen, services.policy.food), markup)


# «🍽 ثبت سفارش غذا» under the menu notification sends OPEN_FOOD_MENU (imported from the code that sends
# it, so the two can't drift apart); it opens the same weekly screen.
@router.callback_query(F.data == OPEN_FOOD_MENU)
@router.callback_query(FoodCb.filter(F.a == "menu"))
async def week_menu(query: CallbackQuery, state: FSMContext, services: Services, user: User) -> None:
    await state.clear()
    await _show_week(query, state, services, user, fresh=True)


@router.callback_query(FoodCb.filter(F.a == "w_reset"))
async def week_reset(query: CallbackQuery, state: FSMContext, services: Services, user: User) -> None:
    await _show_week(query, state, services, user, fresh=True)


@router.callback_query(FoodCb.filter(F.a == "w"))
async def week_toggle(
    query: CallbackQuery, callback_data: FoodCb, state: FSMContext, services: Services, user: User
) -> None:
    days, chosen = await _week_state(state, services, user)
    item_id = UUID(hex=callback_data.v)
    day = next((d for d in days if any(i.id == item_id for i in d.items)), None)
    if day is None:
        raise ExpiredActionError("مهلت سفارش این روز تمام شده یا منو تغییر کرده است. منو را دوباره باز کنید.")
    picked = list(chosen.get(day.day, []))
    maximum = services.policy.food.max_items_per_employee_order
    if item_id in picked:
        picked.remove(item_id)
    elif maximum == 1:
        picked = [item_id]  # one meal per day: the new choice replaces the old one
    elif len(picked) < maximum:
        picked.append(item_id)
    else:
        await query.answer(f"حداکثر {to_persian_digits(maximum)} غذا برای هر روز", show_alert=True)
        return
    chosen[day.day] = picked
    await _save_week_state(state, days, chosen)
    await show(query, texts.week_order(days, chosen, services.policy.food), _week_markup(days, chosen))


@router.callback_query(FoodCb.filter(F.a == "w_ok"))
async def week_confirm(query: CallbackQuery, state: FSMContext, services: Services, user: User) -> None:
    # Save exactly what the person ticked, even if a day closed meanwhile: that day is then reported as
    # not saved (with its deadline) instead of being dropped silently.
    stored = (await state.get_data()).get(WEEK_KEY)
    if stored:
        choices = {date.fromordinal(int(k)): [UUID(hex=h) for h in v] for k, v in stored["chosen"].items()}
    else:
        days_now, chosen_now = await _week_state(state, services, user)
        choices = {d.day: chosen_now.get(d.day, []) for d in days_now}
    result = await services.food.save_week(user, choices)
    days = await services.food.week_menu() + await services.food.week_menu(services.time.today() + timedelta(days=7))
    current = await services.food.current_choices(user, list(choices))
    prices = {i.id: i.price_toman for d in days for i in d.items}
    total = sum(prices.get(item_id, 0) for ids in current.values() for item_id in ids)
    await state.update_data({WEEK_KEY: None})
    markup = inline(
        [
            [button("🧾 صورت‌حساب من", FoodCb(a="invoice").pack())],
            [button("📋 منو و سفارش هفته", FoodCb(a="menu").pack())],
            _back(),
        ]
    )
    await show(query, texts.week_saved(result, total, services.policy.food), markup)


@router.callback_query(FoodCb.filter(F.a == "today"))
async def today_menu(query: CallbackQuery, services: Services, user: User) -> None:
    await _show_day(query, services, user, services.time.today())


@router.callback_query(FoodCb.filter(F.a == "day"))
async def day_menu(query: CallbackQuery, callback_data: FoodCb, services: Services, user: User) -> None:
    await _show_day(query, services, user, date.fromordinal(int(callback_data.v)))


async def _show_day(query: CallbackQuery, services: Services, user: User, day: date) -> None:
    entry = await services.food.day_menu(day)
    if entry is None:
        await show(query, f"برای {format_date(day)} منویی ثبت نشده است.", inline([_back()]))
        return
    current = await services.food.get_order(user, day)
    rows = []
    if entry.is_open:
        chosen = {i.menu_item_id for i in current.items} if current else set()
        for item in entry.items:
            mark = "✅ " if item.id in chosen else ""
            rows.append(
                [
                    button(
                        f"{mark}{item.food_name} — {format_money(item.price_toman, services.policy.food.currency_label)}",
                        FoodCb(a="item", v=item.id.hex).pack(),
                    )
                ]
            )
        if current is not None and services.policy.food.allow_order_edit_before_deadline:
            rows.append([button("🗑 لغو سفارش این روز", FoodCb(a="cancel", v=str(day.toordinal())).pack())])
    rows.append(_back())
    await show(query, texts.day_menu(entry, services.policy.food, current), inline(rows))


@router.callback_query(FoodCb.filter(F.a == "item"))
async def choose_item(query: CallbackQuery, callback_data: FoodCb, services: Services) -> None:
    item = await services.food.repo.get_item(UUID(hex=callback_data.v))
    if item is None:
        raise NotFoundError("این غذا دیگر در منو نیست.")
    text = texts.order_confirm(format_date(item.menu_date), item.food_name, item.price_toman, services.policy.food)
    await show(query, text, inline(confirm_rows(FoodCb(a="ok", v=item.id.hex).pack())))


@router.callback_query(FoodCb.filter(F.a == "ok"))
async def confirm_item(query: CallbackQuery, callback_data: FoodCb, services: Services, user: User) -> None:
    result = await services.food.place_order(user, UUID(hex=callback_data.v))
    text = texts.order_saved(result.order, services.policy.food)
    if not result.changed:
        text = "ℹ️ همین غذا قبلاً برای این روز ثبت شده بود.\n" + text
    await show(query, text, inline([_back()]))


@router.callback_query(FoodCb.filter(F.a == "cancel"))
async def cancel_day(query: CallbackQuery, callback_data: FoodCb, services: Services, user: User) -> None:
    order = await services.food.cancel_order(user, date.fromordinal(int(callback_data.v)))
    await show(query, f"سفارش {format_date(order.order_date)} لغو شد.", inline([_back()]))


# --- guest orders ---------------------------------------------------------------------------------


GUEST_KEY = "food_guest_week"  # FSM data: {"days": [ordinals], "counts": {item hex: guests}}


def _guest_markup(days, counts: dict[UUID, int]):
    rows = []
    for day in days:
        for item in day.items:
            n = counts.get(item.id, 0)
            label = f"➕ {format_date_short(day.day)} · {item.food_name} · {format_number(item.price_toman)}"
            if n:
                label += f" · ×{to_persian_digits(n)}"
            # Soroush shows at most two buttons in a row.
            rows.append(
                [
                    button(label, FoodCb(a="gp", v=item.id.hex).pack()),
                    button("➖", FoodCb(a="gm", v=item.id.hex).pack()),
                ]
            )
    portions = sum(counts.values())
    rows.append([button(f"✅ ثبت سفارش مهمان ({to_persian_digits(portions)} پرس)", FoodCb(a="g_wok").pack())])
    rows.append([button("↩️ برگرداندن به سفارش‌های ثبت‌شده", FoodCb(a="g_wreset").pack())])
    rows.append(_back())
    return inline(rows)


async def _guest_state(state: FSMContext, services: Services, user: User, fresh: bool = False):
    """The days open for guests and the counts chosen so far (kept between taps until confirmed)."""
    days = await services.food.upcoming_menu(guest=True)
    stored = (await state.get_data()).get(GUEST_KEY)
    ordinals = [d.day.toordinal() for d in days]
    offered = {i.id for d in days for i in d.items}
    if fresh or not stored or stored.get("days") != ordinals:
        current = await services.food.current_guest_counts(user, [d.day for d in days])
        counts = {k: v for k, v in current.items() if k in offered}
    else:
        counts = {UUID(hex=h): int(n) for h, n in stored["counts"].items()}
    return days, counts


async def _save_guest_state(state: FSMContext, days, counts: dict[UUID, int]) -> None:
    await state.update_data(
        {GUEST_KEY: {"days": [d.day.toordinal() for d in days], "counts": {k.hex: v for k, v in counts.items()}}}
    )


async def _show_guest(query: CallbackQuery, state: FSMContext, services: Services, user: User, fresh: bool) -> None:
    days, counts = await _guest_state(state, services, user, fresh=fresh)
    await _save_guest_state(state, days, counts)
    markup = _guest_markup(days, counts) if days else inline([_back()])
    await show(query, texts.guest_week(days, counts, services.policy.food), markup)


@router.callback_query(FoodCb.filter(F.a == "guest"))
async def guest_start(query: CallbackQuery, state: FSMContext, services: Services, user: User) -> None:
    await state.clear()
    if not services.policy.food.allow_guest_orders:
        await show(query, "ثبت سفارش مهمان فعلاً فعال نیست.", inline([_back()]))
        return
    await _show_guest(query, state, services, user, fresh=True)


@router.callback_query(FoodCb.filter(F.a == "g_wreset"))
async def guest_reset(query: CallbackQuery, state: FSMContext, services: Services, user: User) -> None:
    await _show_guest(query, state, services, user, fresh=True)


@router.callback_query(FoodCb.filter(F.a.in_({"gp", "gm"})))
async def guest_change(
    query: CallbackQuery, callback_data: FoodCb, state: FSMContext, services: Services, user: User
) -> None:
    days, counts = await _guest_state(state, services, user)
    item_id = UUID(hex=callback_data.v)
    if not any(i.id == item_id for d in days for i in d.items):
        raise ExpiredActionError("مهلت سفارش مهمان این روز تمام شده یا منو تغییر کرده است. دوباره باز کنید.")
    n = counts.get(item_id, 0)
    maximum = services.policy.food.max_guest_count_per_order
    if callback_data.a == "gp":
        if n >= maximum:
            await query.answer(f"حداکثر {to_persian_digits(maximum)} نفر برای هر غذا", show_alert=True)
            return
        n += 1
    else:
        if n == 0:
            await query.answer("برای این غذا مهمانی انتخاب نشده است.")
            return
        n -= 1
    if n:
        counts[item_id] = n
    else:
        counts.pop(item_id, None)
    await _save_guest_state(state, days, counts)
    await show(query, texts.guest_week(days, counts, services.policy.food), _guest_markup(days, counts))


@router.callback_query(FoodCb.filter(F.a == "g_wok"))
async def guest_week_confirm(query: CallbackQuery, state: FSMContext, services: Services, user: User) -> None:
    stored = (await state.get_data()).get(GUEST_KEY)
    if stored is None:
        raise ExpiredActionError("این صفحه منقضی شده است. «👥 سفارش مهمان» را دوباره باز کنید.")
    days = [date.fromordinal(int(o)) for o in stored["days"]]
    wanted = {UUID(hex=h): int(n) for h, n in stored["counts"].items()}
    # Dishes that had guests and are now 0 must be cancelled too: send them as 0.
    orders = await services.food.repo.guest_orders_between(min(days), max(days), user.id) if days else []
    current = {o.menu_item_id for o in orders if o.order_date in days}
    counts = {item_id: wanted.get(item_id, 0) for item_id in {*current, *wanted}}
    result = await services.food.save_guest_week(user, counts)
    total = 0
    if days:
        orders = await services.food.repo.guest_orders_between(min(days), max(days), user.id)
        total = sum(o.total_price for o in orders)
    await state.update_data({GUEST_KEY: None})
    markup = inline(
        [
            [button("🧾 صورت‌حساب من", FoodCb(a="invoice").pack())],
            [button("👥 سفارش مهمان", FoodCb(a="guest").pack())],
            _back(),
        ]
    )
    await show(query, texts.guest_week_saved(result, total, services.policy.food), markup)


# The step-by-step guest screens below are no longer opened from the menu; they stay so buttons in
# messages sent before this change still work.


@router.callback_query(FoodCb.filter(F.a == "g_day"))
async def guest_day(query: CallbackQuery, callback_data: FoodCb, services: Services) -> None:
    day = date.fromordinal(int(callback_data.v))
    entry = await services.food.day_menu(day, guest=True)
    if entry is None or not entry.is_open:
        raise ExpiredActionError("مهلت سفارش این روز تمام شده یا منو تغییر کرده است.")
    currency = services.policy.food.currency_label
    rows = [
        [button(f"{i.food_name} — {format_money(i.price_toman, currency)}", FoodCb(a="g_item", v=i.id.hex).pack())]
        for i in entry.items
    ]
    await show(query, f"👥 {format_date(day)}\nغذای مهمان را انتخاب کنید:", inline([*rows, _back()]))


@router.callback_query(FoodCb.filter(F.a == "g_item"))
async def guest_item(query: CallbackQuery, callback_data: FoodCb, state: FSMContext, services: Services) -> None:
    item = await services.food.repo.get_item(UUID(hex=callback_data.v))
    if item is None:
        raise NotFoundError("این غذا دیگر در منو نیست.")
    maximum = services.policy.food.max_guest_count_per_order
    counts = [
        button(to_persian_digits(n), FoodCb(a="g_cnt", v=f"{item.id.hex}.{n}").pack())
        for n in range(1, min(maximum, 10) + 1)
    ]
    await state.set_state(GuestFlow.typing_count)
    await state.update_data(guest_item=item.id.hex)
    await show(
        query,
        f"👥 {item.food_name} — {format_date(item.menu_date)}\nتعداد مهمان را انتخاب کنید یا عدد آن را بنویسید (حداکثر {to_persian_digits(maximum)}):",
        inline([*chunk(counts, 5), cancel_row()]),
    )


async def _guest_review(services: Services, item_hex: str, count: int) -> tuple[str, object]:
    services.food.validate_guest_count(count)
    item = await services.food.repo.get_item(UUID(hex=item_hex))
    if item is None:
        raise NotFoundError("این غذا دیگر در منو نیست.")
    text = texts.guest_review(
        format_date(item.menu_date), item.food_name, item.price_toman, count, services.policy.food
    )
    return text, inline(confirm_rows(FoodCb(a="g_ok", v=f"{item_hex}.{count}").pack()))


@router.callback_query(FoodCb.filter(F.a == "g_cnt"))
async def guest_count_button(
    query: CallbackQuery, callback_data: FoodCb, state: FSMContext, services: Services
) -> None:
    item_hex, count = callback_data.v.split(".")
    await state.clear()
    text, markup = await _guest_review(services, item_hex, int(count))
    await show(query, text, markup)  # type: ignore[arg-type]


@router.message(StateFilter(GuestFlow.typing_count), F.text)
async def guest_count_typed(message: Message, state: FSMContext, services: Services) -> None:
    count = parse_int(message.text or "")
    if count is None:
        await message.answer("لطفاً تعداد را به عدد بنویسید.")
        return
    data = await state.get_data()
    await state.clear()
    text, markup = await _guest_review(services, data["guest_item"], count)
    await message.answer(text, reply_markup=markup)  # type: ignore[arg-type]


@router.callback_query(FoodCb.filter(F.a == "g_ok"))
async def guest_confirm(query: CallbackQuery, callback_data: FoodCb, services: Services, user: User) -> None:
    item_hex, count = callback_data.v.split(".")
    order = await services.food.place_guest_order(user, UUID(hex=item_hex), int(count))
    await show(query, texts.guest_saved(order, services.policy.food), inline([_back()]))


@router.callback_query(FoodCb.filter(F.a == "g_del"))
async def guest_delete(query: CallbackQuery, callback_data: FoodCb, services: Services, user: User) -> None:
    order = await services.food.cancel_guest_order(user, UUID(hex=callback_data.v))
    await show(query, f"سفارش مهمان {order.food_name} برای {format_date(order.order_date)} لغو شد.", inline([_back()]))


# --- the bill (one place: own meals, guests, cancelling a guest order, next week) ---------------


@router.callback_query(FoodCb.filter(F.a.in_({"invoice", "week"})))  # «week»: the old report's buttons
async def my_invoice(query: CallbackQuery, callback_data: FoodCb, services: Services, user: User) -> None:
    today = services.time.today()
    next_week = callback_data.v == "next"
    invoice = await services.food.invoice(user, today + timedelta(days=7) if next_week else today)
    title = "صورت‌حساب غذای هفته‌ی بعد" if next_week else "صورت‌حساب غذای این هفته"
    text = texts.invoice(invoice, services.policy.food, services.policy.payment, title=title)
    guests = await services.food.repo.guest_orders_between(invoice.week_start, invoice.week_end, user.id)
    rows = [
        [button(f"🗑 لغو مهمان {format_date_short(g.order_date)} {g.food_name}", FoodCb(a="g_del", v=g.id.hex).pack())]
        for g in guests
        if services.food.is_open(g.order_date, guest=True)
    ]
    if next_week:
        rows.append([button("🧾 صورت‌حساب این هفته", FoodCb(a="invoice").pack())])
    else:
        upcoming = await services.food.invoice(user, today + timedelta(days=7))
        if upcoming.lines:
            rows.append([button("🧾 صورت‌حساب هفته‌ی بعد", FoodCb(a="invoice", v="next").pack())])
    await show(query, text, inline([*rows, _back()]))
