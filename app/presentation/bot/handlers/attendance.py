"""Attendance module UI. Every rule lives in AttendanceService; this file only drives the dialog."""

from datetime import UTC, date, datetime, time

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.application.dto.attendance import CorrectionInput
from app.application.presenters import attendance as texts
from app.application.services.container import Services
from app.core.exceptions import ExpiredActionError
from app.core.utils.jalali import format_date
from app.domain.attendance.models import CorrectionStatus, CorrectionType
from app.domain.users.models import User
from app.infrastructure.excel.reports import build_attendance_workbook, report_filename
from app.presentation.bot.callbacks.data import OPEN_ATTENDANCE, AttCb, CalCb, CorrCb, NavCb, TimeCb
from app.presentation.bot.handlers.common import (
    parse_hhmm,
    picker_range,
    query_message,
    send_file,
    show,
    start_picker,
)
from app.presentation.bot.keyboards.common import back_row, button, cancel_row, confirm_rows, inline
from app.presentation.bot.keyboards.pickers import calendar_for, hour_picker
from app.presentation.bot.middlewares.filters import IsRegistered
from app.presentation.bot.modules import BTN_ATTENDANCE
from app.presentation.bot.states.flows import Correction

router = Router(name="attendance")
router.message.filter(IsRegistered())
router.callback_query.filter(IsRegistered())

MENU_TEXT = "⏱ ورود و خروج\nیکی از گزینه‌ها را انتخاب کنید:"
KIND_BY_CODE = {"in": CorrectionType.CHECK_IN, "out": CorrectionType.CHECK_OUT, "both": CorrectionType.BOTH}


def menu_markup():
    return inline(
        [
            [button("✅ ثبت ورود", AttCb(a="in").pack()), button("🏁 ثبت خروج", AttCb(a="out").pack())],
            [button("📝 ثبت فراموشی ورود/خروج", AttCb(a="forgot").pack())],
            [button("📊 گزارش هفتگی من", AttCb(a="week").pack()), button("📋 وضعیت امروز", AttCb(a="status").pack())],
            back_row(),
        ]
    )


def back_to_menu():
    return inline([back_row("att")])


@router.message(F.text == BTN_ATTENDANCE)
async def open_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(MENU_TEXT, reply_markup=menu_markup())


@router.callback_query(F.data == OPEN_ATTENDANCE)
@router.callback_query(NavCb.filter(F.to == "att"))
async def open_menu_from_button(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show(query, MENU_TEXT, menu_markup())


# --- live check-in / check-out --------------------------------------------------------------


@router.callback_query(AttCb.filter(F.a == "in"))
async def check_in_preview(query: CallbackQuery, services: Services, user: User) -> None:
    preview = await services.attendance.preview_check_in(user)
    if preview.existing is not None:
        at = services.time.to_local(preview.existing.check_in_at)
        await show(query, texts.already_registered("in", at), back_to_menu())
        return
    data = AttCb(a="in_ok", ts=int(preview.at_utc.timestamp())).pack()
    await show(query, texts.punch_preview(preview), inline(confirm_rows(data)))


@router.callback_query(AttCb.filter(F.a == "in_ok"))
async def check_in_confirm(query: CallbackQuery, callback_data: AttCb, services: Services, user: User) -> None:
    result = await services.attendance.check_in(user, datetime.fromtimestamp(callback_data.ts, UTC))
    at = services.time.to_local(result.record.check_in_at)
    text = texts.punch_saved("in", at, result.calculation) if result.created else texts.already_registered("in", at)
    await show(query, text, back_to_menu())


@router.callback_query(AttCb.filter(F.a == "out"))
async def check_out_preview(query: CallbackQuery, services: Services, user: User) -> None:
    preview = await services.attendance.preview_check_out(user)
    if preview.existing is not None:
        at = services.time.to_local(preview.existing.check_out_at)
        await show(query, texts.already_registered("out", at), back_to_menu())
        return
    data = AttCb(a="out_ok", ts=int(preview.at_utc.timestamp()), d=preview.work_date.toordinal()).pack()
    await show(query, texts.punch_preview(preview), inline(confirm_rows(data)))


@router.callback_query(AttCb.filter(F.a == "out_ok"))
async def check_out_confirm(query: CallbackQuery, callback_data: AttCb, services: Services, user: User) -> None:
    result = await services.attendance.check_out(
        user, datetime.fromtimestamp(callback_data.ts, UTC), date.fromordinal(callback_data.d)
    )
    at = services.time.to_local(result.record.check_out_at)
    text = texts.punch_saved("out", at, result.calculation) if result.created else texts.already_registered("out", at)
    await show(query, text, back_to_menu())


# --- status and weekly report ------------------------------------------------------------------


@router.callback_query(AttCb.filter(F.a == "status"))
async def today_status(query: CallbackQuery, services: Services, user: User) -> None:
    today = services.time.today()
    record = await services.attendance.record_for(user, today)
    day = today
    if record is None or record.check_in_at is None:
        night = await services.attendance.open_night_shift(user)
        if night is not None:
            record, day = night, night.work_date
    calc = services.attendance.calculate(user, day, record)
    await show(query, texts.today_status(day, calc), back_to_menu())


@router.callback_query(AttCb.filter(F.a == "week"))
async def weekly_report(query: CallbackQuery, services: Services, user: User) -> None:
    report = await services.attendance.my_week(user)
    markup = inline([[button("📥 دریافت فایل اکسل", AttCb(a="week_xls").pack())], back_row("att")])
    await show(query, texts.employee_report(report), markup)


@router.callback_query(AttCb.filter(F.a == "week_xls"))
async def weekly_excel(query: CallbackQuery, services: Services, user: User) -> None:
    report = await services.attendance.my_week(user)
    await query.answer("در حال آماده‌سازی فایل…")
    content = build_attendance_workbook([report])
    await send_file(query_message(query), content, report_filename("attendance_me", report.start, report.end))


# --- forgotten check-in / check-out ------------------------------------------------------------


@router.callback_query(AttCb.filter(F.a == "forgot"))
async def correction_start(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    markup = inline(
        [
            [
                button("ورود", CorrCb(a="kind", k="in").pack()),
                button("خروج", CorrCb(a="kind", k="out").pack()),
                button("هر دو", CorrCb(a="kind", k="both").pack()),
            ],
            cancel_row(),
        ]
    )
    await show(query, "📝 ثبت فراموشی\nکدام را فراموش کرده‌اید؟", markup)


@router.callback_query(CorrCb.filter(F.a == "kind"))
async def correction_kind(query: CallbackQuery, callback_data: CorrCb, state: FSMContext, services: Services) -> None:
    if callback_data.k not in KIND_BY_CODE:
        raise ExpiredActionError()
    min_date, max_date = services.attendance.correction_date_window()
    await state.set_state(Correction.choosing_date)
    await state.update_data(kind=callback_data.k)
    await start_picker(state, min_date, max_date)
    await show(
        query,
        "📅 تاریخ روزی را که ثبت نشده انتخاب کنید:",
        calendar_for("corr", services.time.today(), min_date, max_date),
    )


@router.callback_query(StateFilter(Correction.choosing_date), CalCb.filter((F.p == "corr") & (F.a == "d")))
async def correction_date(query: CallbackQuery, callback_data: CalCb, state: FSMContext) -> None:
    min_date, max_date = await picker_range(state)
    day = date.fromordinal(callback_data.v)
    if not min_date <= day <= max_date:
        raise ExpiredActionError()
    data = await state.get_data()
    await state.update_data(day=callback_data.v)
    await state.set_state(Correction.choosing_time)
    step = "out" if data["kind"] == "out" else "in"
    await _ask_time(query, step, day)


async def _ask_time(query: CallbackQuery, step: str, day: date) -> None:
    label = "ورود" if step == "in" else "خروج"
    await show(
        query,
        f"🕘 ساعت {label} روز {format_date(day)} را انتخاب کنید.\nیا ساعت دقیق را به شکل ۰۸:۴۵ بنویسید.",
        hour_picker(f"corr_{step}"),
    )


@router.callback_query(StateFilter(Correction.choosing_time), TimeCb.filter((F.a == "m") & F.p.startswith("corr_")))
async def correction_time_button(query: CallbackQuery, callback_data: TimeCb, state: FSMContext) -> None:
    step = callback_data.p.removeprefix("corr_")
    await _time_chosen(state, step, time(callback_data.h, callback_data.m), query=query)


@router.message(StateFilter(Correction.choosing_time), F.text)
async def correction_time_typed(message: Message, state: FSMContext) -> None:
    value = parse_hhmm(message.text or "")
    if value is None:
        await message.answer("ساعت را به شکل ۰۸:۴۵ بنویسید یا از دکمه‌ها انتخاب کنید.")
        return
    data = await state.get_data()
    step = "out" if data["kind"] == "out" or "in_time" in data else "in"
    await _time_chosen(state, step, value, message=message)


async def _time_chosen(
    state: FSMContext, step: str, value: time, *, query: CallbackQuery | None = None, message: Message | None = None
) -> None:
    await state.update_data({f"{step}_time": value.strftime("%H:%M")})
    data = await state.get_data()
    day = date.fromordinal(data["day"])
    if data["kind"] == "both" and step == "in":
        if query is not None:
            await _ask_time(query, "out", day)
        elif message is not None:
            await message.answer(
                f"🕘 حالا ساعت خروج روز {format_date(day)} را انتخاب کنید یا بنویسید:",
                reply_markup=hour_picker("corr_out"),
            )
        return
    await state.set_state(Correction.confirming)
    text = texts.correction_review(KIND_BY_CODE[data["kind"]], day, data.get("in_time"), data.get("out_time"))
    markup = inline(confirm_rows(CorrCb(a="ok").pack()))
    if query is not None:
        await show(query, text, markup)
    elif message is not None:
        await message.answer(text, reply_markup=markup)


@router.callback_query(StateFilter(Correction.confirming), CorrCb.filter(F.a == "ok"))
async def correction_confirm(query: CallbackQuery, state: FSMContext, services: Services, user: User) -> None:
    data = await state.get_data()
    await state.clear()

    def to_time(value: str | None) -> time | None:
        return time.fromisoformat(value) if value else None

    result = await services.attendance.submit_correction(
        user,
        CorrectionInput(
            correction_type=KIND_BY_CODE[data["kind"]],
            work_date=date.fromordinal(data["day"]),
            check_in_time=to_time(data.get("in_time")),
            check_out_time=to_time(data.get("out_time")),
        ),
    )
    if result.status == CorrectionStatus.PENDING_APPROVAL:
        text = "📨 ثبت فراموشی شما ذخیره شد و پس از تأیید مدیر اعمال می‌شود."
    else:
        text = "✅ ثبت فراموشی انجام شد و در گزارش‌ها با علامت ✍️ نمایش داده می‌شود."
    await show(query, text, back_to_menu())


@router.callback_query(CorrCb.filter(F.a == "ok"))
@router.callback_query(CalCb.filter(F.p == "corr"))
@router.callback_query(TimeCb.filter(F.p.startswith("corr_")))
async def correction_stale(query: CallbackQuery) -> None:
    raise ExpiredActionError()
