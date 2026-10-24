"""Leave module UI: new request wizard, history and status. Rules live in LeaveService."""

from datetime import date, time
from uuid import UUID

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.application.dto.leave import LeaveDraft, SubmitResult
from app.application.dto.messaging import InlineButtons
from app.application.presenters import leave as texts
from app.application.services.container import Services
from app.core.exceptions import ExpiredActionError, ValidationError
from app.core.utils.jalali import format_date
from app.core.utils.text import to_persian_digits
from app.domain.leave.models import OPEN_LEAVE_STATUSES, LeavePayType, LeaveRequest, LeaveUnit
from app.domain.notifications.models import NotificationType
from app.domain.users.models import User
from app.presentation.bot.callbacks.data import ApprovalCb, CalCb, LeaveCb, NavCb, TimeCb
from app.presentation.bot.handlers.common import check_hhmm, parse_typed_date, picker_range, reply, show, start_picker
from app.presentation.bot.keyboards.common import back_row, button, confirm_rows, inline, step_row
from app.presentation.bot.keyboards.pickers import TYPE_DATE_HINT, calendar_for, hour_picker
from app.presentation.bot.middlewares.filters import IsRegistered
from app.presentation.bot.modules import BTN_LEAVE
from app.presentation.bot.states.flows import LeaveFlow

router = Router(name="leave")
router.message.filter(IsRegistered())
router.callback_query.filter(IsRegistered())

MENU_TEXT = "📝 مرخصی\nیکی از گزینه‌ها را انتخاب کنید:"


def menu_markup():
    return inline(
        [
            [button("➕ درخواست مرخصی جدید", LeaveCb(a="new").pack())],
            [
                button("🗂 درخواست‌های من", LeaveCb(a="mine").pack()),
                button("⏳ وضعیت مرخصی‌ها", LeaveCb(a="status").pack()),
            ],
            back_row(),
        ]
    )


def approval_buttons(request: LeaveRequest, step_order: int) -> InlineButtons:
    entity = request.id.hex
    return InlineButtons.of(
        [
            ("✅ تأیید", ApprovalCb(w="lv", e=entity, s=step_order, a="y").pack()),
            ("❌ رد", ApprovalCb(w="lv", e=entity, s=step_order, a="n").pack()),
        ]
    )


async def notify_approvers(
    services: Services, request: LeaveRequest, employee: User, approvers: list[User], step_order: int, step_title: str
) -> int:
    text = "📥 درخواست جدید برای بررسی\n\n" + texts.request_details(request, employee, step_title)
    sent = 0
    for approver in approvers:
        outcome = await services.notifications.notify_user(
            approver,
            text,
            NotificationType.LEAVE_APPROVAL_REQUEST,
            buttons=approval_buttons(request, step_order),
            dedup_key=f"leave_approval:{request.id}:{step_order}:{approver.id}",
        )
        sent += outcome == "SENT"
    return sent


@router.message(F.text == BTN_LEAVE)
async def open_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(MENU_TEXT, reply_markup=menu_markup())


@router.callback_query(NavCb.filter(F.to == "leave"))
async def open_menu_button(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show(query, MENU_TEXT, menu_markup())


# --- new request wizard ---------------------------------------------------------------------


# Steps: pay type → unit → date(s) → times (hourly) → reason → confirm. Each step can go back.
REASON_PROMPT = "✍️ توضیح یا دلیل مرخصی را بنویسید (اختیاری):"


def _back(step: str) -> str:
    return LeaveCb(a="back", v=step).pack()


@router.callback_query(LeaveCb.filter(F.a == "new"))
async def new_request(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(LeaveFlow.choosing)
    await _show_pay(query)


async def _show_pay(target: CallbackQuery | Message) -> None:
    markup = inline(
        [
            [
                button("💰 با حقوق", LeaveCb(a="pay", v="PAID").pack()),
                button("بی‌حقوق", LeaveCb(a="pay", v="UNPAID").pack()),
            ],
            step_row(NavCb(to="leave").pack()),
        ]
    )
    await reply(target, "نوع مرخصی را انتخاب کنید:", markup)


@router.callback_query(StateFilter(LeaveFlow.choosing), LeaveCb.filter(F.a == "pay"))
async def choose_pay(query: CallbackQuery, callback_data: LeaveCb, state: FSMContext) -> None:
    pay = LeavePayType(callback_data.v)
    await state.update_data(pay=pay.value, awaiting=None)
    await _show_unit(query)


async def _show_unit(target: CallbackQuery | Message) -> None:
    markup = inline(
        [
            [
                button("⏰ ساعتی", LeaveCb(a="unit", v="HOURLY").pack()),
                button("📅 روزانه", LeaveCb(a="unit", v="DAILY").pack()),
            ],
            step_row(_back("pay")),
        ]
    )
    await reply(target, "مرخصی ساعتی است یا روزانه؟", markup)


@router.callback_query(StateFilter(LeaveFlow.choosing), LeaveCb.filter(F.a == "unit"))
async def choose_unit(query: CallbackQuery, callback_data: LeaveCb, state: FSMContext, services: Services) -> None:
    unit = LeaveUnit(callback_data.v)
    await state.update_data(unit=unit.value)
    await _show_start_date(query, state, services)


async def _show_start_date(target: CallbackQuery | Message, state: FSMContext, services: Services) -> None:
    hourly = (await state.get_data()).get("unit") == LeaveUnit.HOURLY.value
    min_date, max_date = services.leave.date_window()
    purpose = "lv_d" if hourly else "lv_s"
    await state.update_data(awaiting=purpose)
    await start_picker(state, min_date, max_date, back=_back("unit"))
    prompt = "📅 تاریخ مرخصی را انتخاب کنید." if hourly else "📅 تاریخ شروع مرخصی را انتخاب کنید."
    markup = calendar_for(purpose, services.time.today(), min_date, max_date, back=_back("unit"))
    await reply(target, f"{prompt}\n{TYPE_DATE_HINT}", markup)


async def _show_end_date(target: CallbackQuery | Message, state: FSMContext, services: Services) -> None:
    start = date.fromordinal((await state.get_data())["start"])
    _, max_date = services.leave.date_window()
    await state.update_data(awaiting="lv_e")
    await start_picker(state, start, max_date, back=_back("start"))
    await reply(
        target,
        f"شروع: {format_date(start)}\n📅 تاریخ پایان مرخصی را انتخاب کنید.\n{TYPE_DATE_HINT}",
        calendar_for("lv_e", start, start, max_date, back=_back("start")),
    )


async def _show_start_time(target: CallbackQuery | Message, state: FSMContext) -> None:
    day = date.fromordinal((await state.get_data())["start"])
    await state.update_data(awaiting="start_time", time_back=_back("start"))
    await reply(
        target,
        f"🕘 ساعت شروع مرخصی ({format_date(day)}) را انتخاب کنید یا بنویسید (مثل ۱۰:۳۰):",
        hour_picker("lv_s", 6, 22, back=_back("start")),
    )


async def _show_end_time(target: CallbackQuery | Message, state: FSMContext) -> None:
    start_time = (await state.get_data())["start_time"]
    await state.update_data(awaiting="end_time", time_back=_back("stime"))
    await reply(
        target,
        f"شروع: {to_persian_digits(start_time)}\n🕘 ساعت پایان را انتخاب کنید یا بنویسید:",
        hour_picker("lv_e", 6, 23, back=_back("stime")),
    )


@router.callback_query(StateFilter(LeaveFlow.choosing), CalCb.filter((F.a == "d") & F.p.in_({"lv_d", "lv_s", "lv_e"})))
async def choose_date(query: CallbackQuery, callback_data: CalCb, state: FSMContext, services: Services) -> None:
    await _date_chosen(query, state, services, callback_data.p, date.fromordinal(callback_data.v))


async def _date_chosen(
    target: CallbackQuery | Message, state: FSMContext, services: Services, purpose: str, day: date
) -> None:
    min_date, max_date = await picker_range(state)
    if not min_date <= day <= max_date:
        await reply(
            target, f"این تاریخ قابل انتخاب نیست. بازه‌ی مجاز: {format_date(min_date)} تا {format_date(max_date)}"
        )
        return
    if purpose == "lv_d":
        await state.update_data(start=day.toordinal(), end=day.toordinal())
        await _show_start_time(target, state)
    elif purpose == "lv_s":
        await state.update_data(start=day.toordinal())
        await _show_end_date(target, state, services)
    else:
        await state.update_data(end=day.toordinal())
        await _ask_reason(target, state)


@router.callback_query(StateFilter(LeaveFlow.choosing), TimeCb.filter((F.a == "m") & F.p.in_({"lv_s", "lv_e"})))
async def choose_time_button(query: CallbackQuery, callback_data: TimeCb, state: FSMContext) -> None:
    await _time_chosen(
        query, state, "start_time" if callback_data.p == "lv_s" else "end_time", time(callback_data.h, callback_data.m)
    )


async def _time_chosen(target: CallbackQuery | Message, state: FSMContext, which: str, value: time) -> None:
    await state.update_data({which: value.strftime("%H:%M")})
    if which == "start_time":
        await _show_end_time(target, state)
    else:
        await _ask_reason(target, state)


async def _ask_reason(target: CallbackQuery | Message, state: FSMContext) -> None:
    hourly = (await state.get_data()).get("unit") == LeaveUnit.HOURLY.value
    await state.update_data(awaiting="reason")
    markup = inline(
        [
            [button("بدون توضیح ⏭", LeaveCb(a="skip_reason").pack())],
            step_row(_back("etime" if hourly else "end")),
        ]
    )
    await reply(target, REASON_PROMPT, markup)


@router.callback_query(StateFilter(LeaveFlow.choosing, LeaveFlow.confirming), LeaveCb.filter(F.a == "back"))
async def step_back(query: CallbackQuery, callback_data: LeaveCb, state: FSMContext, services: Services) -> None:
    data = await state.get_data()
    await state.set_state(LeaveFlow.choosing)
    step = callback_data.v
    if step == "pay":
        await _show_pay(query)
    elif step == "unit" and "pay" in data:
        await _show_unit(query)
    elif step == "start" and "unit" in data:
        await _show_start_date(query, state, services)
    elif step == "end" and "start" in data:
        await _show_end_date(query, state, services)
    elif step == "stime" and "start" in data:
        await _show_start_time(query, state)
    elif step == "etime" and "start_time" in data:
        await _show_end_time(query, state)
    elif step == "reason" and "unit" in data:
        await _ask_reason(query, state)
    else:
        raise ExpiredActionError()


@router.message(StateFilter(LeaveFlow.choosing), F.text)
async def typed_input(message: Message, state: FSMContext, services: Services, user: User) -> None:
    data = await state.get_data()
    awaiting = data.get("awaiting")
    text = message.text or ""
    if awaiting in ("start_time", "end_time"):
        value, problem = check_hhmm(text)
        if problem:
            await message.answer(problem)
            return
        await _time_chosen(message, state, awaiting, value)
        return
    if awaiting in ("lv_d", "lv_s", "lv_e"):
        day = parse_typed_date(text)
        if day is None:
            await message.answer("تاریخ را به شکل ۱۴۰۵/۰۷/۲۶ بنویسید یا از دکمه‌ها انتخاب کنید.")
            return
        await _date_chosen(message, state, services, awaiting, day)
        return
    if awaiting == "reason":
        await state.update_data(reason=text[: services.policy.leave.max_reason_length])
        await _review(message, state, services, user)
        return
    await message.answer("لطفاً از دکمه‌ها استفاده کنید یا /cancel را بزنید.")


@router.callback_query(StateFilter(LeaveFlow.choosing), LeaveCb.filter(F.a == "skip_reason"))
async def skip_reason(query: CallbackQuery, state: FSMContext, services: Services, user: User) -> None:
    await state.update_data(reason=None)
    await query.answer()
    if isinstance(query.message, Message):
        await _review(query.message, state, services, user, edit=True)


def _draft(data: dict) -> LeaveDraft:
    try:
        return LeaveDraft(
            pay_type=LeavePayType(data["pay"]),
            unit=LeaveUnit(data["unit"]),
            start_date=date.fromordinal(data["start"]),
            end_date=date.fromordinal(data["end"]),
            start_time=time.fromisoformat(data["start_time"]) if data.get("start_time") else None,
            end_time=time.fromisoformat(data["end_time"]) if data.get("end_time") else None,
            reason=data.get("reason"),
        )
    except KeyError:
        raise ExpiredActionError() from None


async def _review(message: Message, state: FSMContext, services: Services, user: User, edit: bool = False) -> None:
    draft = _draft(await state.get_data())
    try:
        quote = await services.leave.preview(user, draft)
    except ValidationError as exc:
        # Keep the answers so the person can go back and change the dates or times.
        markup = inline([step_row(_back("start"))])
        if edit:
            await message.edit_text(f"❌ {exc.user_message}", reply_markup=markup)
        else:
            await message.answer(f"❌ {exc.user_message}", reply_markup=markup)
        return
    await state.set_state(LeaveFlow.confirming)
    markup = inline(confirm_rows(LeaveCb(a="submit").pack(), _back("reason")))
    if edit:
        await message.edit_text(texts.quote_review(quote), reply_markup=markup)
    else:
        await message.answer(texts.quote_review(quote), reply_markup=markup)


@router.callback_query(StateFilter(LeaveFlow.confirming), LeaveCb.filter(F.a == "submit"))
async def submit(query: CallbackQuery, state: FSMContext, services: Services, user: User) -> None:
    draft = _draft(await state.get_data())
    await state.clear()
    result: SubmitResult = await services.leave.submit(user, draft)
    await show(query, texts.submitted_to_employee(result.request), inline([back_row("leave")]))
    title = services.leave.workflow.step(result.first_step.step_order).title_fa
    sent = await notify_approvers(services, result.request, user, result.approvers, result.first_step.step_order, title)
    if sent == 0 and isinstance(query.message, Message):
        await query.message.answer("⚠️ هنوز ادمینی برای بررسی مرخصی تعریف نشده است. لطفاً به مدیر سیستم اطلاع دهید.")


@router.callback_query(LeaveCb.filter(F.a.in_({"submit", "pay", "unit", "skip_reason"})))
async def stale_wizard(query: CallbackQuery) -> None:
    raise ExpiredActionError()


# --- history and status -----------------------------------------------------------------------


@router.callback_query(LeaveCb.filter(F.a == "mine"))
async def my_requests(query: CallbackQuery, services: Services, user: User) -> None:
    requests = await services.leave.my_requests(user)
    if not requests:
        await show(query, "شما هنوز درخواست مرخصی ثبت نکرده‌اید.", inline([back_row("leave")]))
        return
    lines = ["🗂 درخواست‌های اخیر شما", ""] + [texts.request_line(r) for r in requests]
    rows = [
        [button(f"مشاهده #{to_persian_digits(r.display_number)}", LeaveCb(a="view", v=r.id.hex).pack())]
        for r in requests[:8]
    ]
    await show(query, "\n".join(lines), inline([*rows, back_row("leave")]))


@router.callback_query(LeaveCb.filter(F.a == "status"))
async def open_requests(query: CallbackQuery, services: Services, user: User) -> None:
    requests = [r for r in await services.leave.my_requests(user, limit=30) if r.status in OPEN_LEAVE_STATUSES]
    if not requests:
        await show(query, "درخواست در انتظار بررسی ندارید.", inline([back_row("leave")]))
        return
    lines = ["⏳ درخواست‌های در جریان", ""] + [texts.request_line(r) for r in requests]
    rows = [
        [button(f"مشاهده #{to_persian_digits(r.display_number)}", LeaveCb(a="view", v=r.id.hex).pack())]
        for r in requests[:8]
    ]
    await show(query, "\n".join(lines), inline([*rows, back_row("leave")]))


@router.callback_query(LeaveCb.filter(F.a == "view"))
async def view_request(query: CallbackQuery, callback_data: LeaveCb, services: Services, user: User) -> None:
    request = await services.leave.get_request(UUID(hex=callback_data.v))
    if request.user_id != user.id:
        raise ExpiredActionError()
    step = await services.leave.current_step(request.id)
    title = services.leave.workflow.step(step.step_order).title_fa if step else None
    rows = []
    if request.status in OPEN_LEAVE_STATUSES:
        rows.append([button("🗑 لغو درخواست", LeaveCb(a="cancel", v=request.id.hex).pack())])
    rows.append(back_row("leave"))
    await show(query, texts.request_details(request, step_title=title), inline(rows))


@router.callback_query(LeaveCb.filter(F.a == "cancel"))
async def cancel_request(query: CallbackQuery, callback_data: LeaveCb, services: Services, user: User) -> None:
    request = await services.leave.cancel(user, UUID(hex=callback_data.v))
    await show(query, f"درخواست شماره {to_persian_digits(request.display_number)} لغو شد.", inline([back_row("leave")]))
