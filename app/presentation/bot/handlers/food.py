"""Food ordering UI for employees. Rules (deadline, one meal per day, guests) live in FoodService."""

from datetime import date
from uuid import UUID

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.application.presenters import food as texts
from app.application.services import access
from app.application.services.container import Services
from app.core.exceptions import ExpiredActionError, NotFoundError
from app.core.security.rbac import Permission
from app.core.utils.jalali import format_date, format_date_short
from app.core.utils.text import format_money, parse_int, to_persian_digits
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
        [button("📋 منوی این هفته", FoodCb(a="menu").pack()), button("🍛 سفارش امروز", FoodCb(a="today").pack())],
        [button("👥 سفارش مهمان", FoodCb(a="guest").pack())],
        [
            button("📊 گزارش غذای این هفته", FoodCb(a="week").pack()),
            button("🧾 صورت‌حساب من", FoodCb(a="invoice").pack()),
        ],
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


@router.callback_query(FoodCb.filter(F.a == "menu"))
async def week_menu(query: CallbackQuery, services: Services) -> None:
    days = await services.food.week_menu()
    upcoming = await services.food.upcoming_menu()
    day_buttons = [
        button(f"📅 {format_date_short(d.day)}", FoodCb(a="day", v=str(d.day.toordinal())).pack()) for d in upcoming
    ]
    rows = chunk(day_buttons, 2)
    if upcoming:
        text = texts.week_menu(days, services.policy.food) + "\n\nبرای سفارش، روز موردنظر را انتخاب کنید:"
    else:
        text = texts.week_menu(days, services.policy.food)
    await show(query, text, inline([*rows, _back()]))


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


@router.callback_query(FoodCb.filter(F.a == "guest"))
async def guest_start(query: CallbackQuery, state: FSMContext, services: Services) -> None:
    await state.clear()
    if not services.policy.food.allow_guest_orders:
        await show(query, "ثبت سفارش مهمان فعلاً فعال نیست.", inline([_back()]))
        return
    days = await services.food.upcoming_menu(guest=True)
    if not days:
        await show(query, "روزی برای سفارش مهمان باز نیست.", inline([_back()]))
        return
    rows = chunk(
        [button(f"📅 {format_date_short(d.day)}", FoodCb(a="g_day", v=str(d.day.toordinal())).pack()) for d in days], 2
    )
    await show(query, "👥 سفارش مهمان\nتاریخ را انتخاب کنید:", inline([*rows, _back()]))


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


# --- employee reports -------------------------------------------------------------------------------


@router.callback_query(FoodCb.filter(F.a == "week"))
async def my_week(query: CallbackQuery, services: Services, user: User) -> None:
    invoice = await services.food.invoice(user)
    text = texts.invoice(invoice, services.policy.food, services.policy.payment, title="گزارش غذای این هفته")
    week_start = invoice.week_start
    guests = await services.food.repo.guest_orders_between(week_start, invoice.week_end, user.id)
    rows = [
        [button(f"🗑 لغو مهمان {format_date_short(g.order_date)} {g.food_name}", FoodCb(a="g_del", v=g.id.hex).pack())]
        for g in guests
        if services.food.is_open(g.order_date, guest=True)
    ]
    await show(query, text, inline([*rows, _back()]))


@router.callback_query(FoodCb.filter(F.a == "invoice"))
async def my_invoice(query: CallbackQuery, services: Services, user: User) -> None:
    invoice = await services.food.invoice(user)
    await show(query, texts.invoice(invoice, services.policy.food, services.policy.payment), inline([_back()]))
