"""Attendance module UI. Every rule lives in AttendanceService; this file only drives the dialog."""

from datetime import UTC, date, datetime, time

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from app.application.dto.attendance import CorrectionInput
from app.application.presenters import attendance as texts
from app.application.services.container import Services
from app.core.exceptions import ConflictError, ExpiredActionError, ValidationError
from app.core.utils.jalali import format_date
from app.core.utils.text import to_persian_digits
from app.domain.attendance.models import CorrectionStatus, CorrectionType
from app.domain.users.models import User
from app.infrastructure.excel.reports import build_attendance_workbook, report_filename
from app.presentation.bot.callbacks.data import OPEN_ATTENDANCE, AttCb, CalCb, CorrCb, NavCb, TimeCb
from app.presentation.bot.handlers.common import (
    TIME_FORMAT_HINT,
    check_hhmm,
    parse_typed_date,
    picker_range,
    query_message,
    reply,
    send_file,
    show,
    start_picker,
)
from app.presentation.bot.keyboards.common import back_row, button, confirm_rows, inline, step_row
from app.presentation.bot.keyboards.pickers import TYPE_DATE_HINT, calendar_for
from app.presentation.bot.middlewares.filters import IsRegistered
from app.presentation.bot.modules import BTN_ATTENDANCE
from app.presentation.bot.states.flows import Correction

router = Router(name="attendance")
router.message.filter(IsRegistered())
router.callback_query.filter(IsRegistered())

MENU_TEXT = "⏱ ورود و خروج\nیکی از گزینه‌ها را انتخاب کنید:"
KIND_BY_CODE = {"in": CorrectionType.CHECK_IN, "out": CorrectionType.CHECK_OUT}


def menu_markup():
    return inline(
        [
            # Soroush draws rows left to right: listing خروج first puts ورود on the right.
            [button("🏁 ثبت خروج امروز", AttCb(a="out").pack()), button("✅ ثبت ورود امروز", AttCb(a="in").pack())],
            [button("📝 ثبت فراموشی ورود/خروج", AttCb(a="forgot").pack())],
            [
                button("📊 گزارش هفتگی من", AttCb(a="week").pack()),
                button("📅 گزارش ماهانه من", AttCb(a="month").pack()),
            ],
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


# --- today's check-in / check-out: automatic (now) or manual (typed time) -----------------------


def _punch_choice(auto_data: str, manual_data: str, at_local: datetime) -> InlineKeyboardMarkup:
    return inline(
        [
            # Soroush draws rows left to right: «دستی» ends up on the right.
            [
                button(f"⏱ اتوماتیک ({to_persian_digits(at_local.strftime('%H:%M'))})", auto_data),
                button("✍️ دستی", manual_data),
            ],
            back_row("att"),
        ]
    )


@router.callback_query(AttCb.filter(F.a == "in"))
async def check_in_preview(query: CallbackQuery, services: Services, user: User) -> None:
    preview = await services.attendance.preview_check_in(user)
    if preview.existing is not None:
        at = services.time.to_local(preview.existing.check_in_at)
        await show(query, texts.already_registered("in", at), back_to_menu())
        return
    auto = AttCb(a="in_ok", ts=int(preview.at_utc.timestamp())).pack()
    manual = AttCb(a="in_man", d=preview.work_date.toordinal()).pack()
    await show(query, texts.punch_preview(preview), _punch_choice(auto, manual, preview.at_local))


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
    auto = AttCb(a="out_ok", ts=int(preview.at_utc.timestamp()), d=preview.work_date.toordinal()).pack()
    manual = AttCb(a="out_man", d=preview.work_date.toordinal()).pack()
    await show(query, texts.punch_preview(preview), _punch_choice(auto, manual, preview.at_local))


@router.callback_query(AttCb.filter(F.a == "out_ok"))
async def check_out_confirm(query: CallbackQuery, callback_data: AttCb, services: Services, user: User) -> None:
    result = await services.attendance.check_out(
        user, datetime.fromtimestamp(callback_data.ts, UTC), date.fromordinal(callback_data.d)
    )
    at = services.time.to_local(result.record.check_out_at)
    text = texts.punch_saved("out", at, result.calculation) if result.created else texts.already_registered("out", at)
    await show(query, text, back_to_menu())


@router.callback_query(AttCb.filter(F.a.in_({"in_man", "out_man"})))
async def punch_manual(query: CallbackQuery, callback_data: AttCb, state: FSMContext) -> None:
    """Manual time for today: the same typed-time step as a forgotten entry, saved as ✍️."""
    if not callback_data.d:
        raise ExpiredActionError()
    await state.clear()
    kind = "in" if callback_data.a == "in_man" else "out"
    await state.update_data(day=callback_data.d, kind=kind, origin="today")
    await _ask_time(query, state)


# --- weekly and monthly report ------------------------------------------------------------------


@router.callback_query(AttCb.filter(F.a == "week"))
async def weekly_report(query: CallbackQuery, services: Services, user: User) -> None:
    report = await services.attendance.my_week(user)
    markup = inline([[button("📥 دریافت فایل اکسل", AttCb(a="week_xls").pack())], back_row("att")])
    await show(query, texts.employee_report(report), markup)


@router.callback_query(AttCb.filter(F.a == "month"))
async def monthly_report(query: CallbackQuery, services: Services, user: User) -> None:
    report = await services.attendance.my_month(user)
    markup = inline([[button("📥 دریافت فایل اکسل", AttCb(a="month_xls").pack())], back_row("att")])
    # A whole month of day lines is too long for one message; the Excel file has the details.
    await show(query, texts.employee_report(report, title="گزارش ماهانه من", with_days=False), markup)


@router.callback_query(AttCb.filter(F.a.in_({"week_xls", "month_xls"})))
async def my_excel(query: CallbackQuery, callback_data: AttCb, services: Services, user: User) -> None:
    if callback_data.a == "month_xls":
        report = await services.attendance.my_month(user)
    else:
        report = await services.attendance.my_week(user)
    await query.answer("در حال آماده‌سازی فایل…")
    content = build_attendance_workbook([report])
    await send_file(query_message(query), content, report_filename("attendance_me", report.start, report.end))


# --- forgotten check-in / check-out ------------------------------------------------------------
# Steps: date → check-in or check-out → typed time → confirm. Every step has "previous step".


def _kind_label(kind: str) -> str:
    return "ورود" if kind == "in" else "خروج"


@router.callback_query(AttCb.filter(F.a == "forgot"))
async def correction_start(query: CallbackQuery, state: FSMContext, services: Services) -> None:
    await state.clear()
    await _ask_date(query, state, services)


async def _ask_date(query: CallbackQuery, state: FSMContext, services: Services) -> None:
    min_date, max_date = services.attendance.correction_date_window()
    back = NavCb(to="att").pack()
    await state.set_state(Correction.choosing_date)
    await start_picker(state, min_date, max_date, back=back)
    await show(
        query,
        f"📝 ثبت فراموشی\n📅 تاریخ روزی را که ثبت نشده انتخاب کنید.\n{TYPE_DATE_HINT}",
        calendar_for("corr", services.time.today(), min_date, max_date, back=back),
    )


@router.callback_query(StateFilter(Correction.choosing_date), CalCb.filter((F.p == "corr") & (F.a == "d")))
async def correction_date(query: CallbackQuery, callback_data: CalCb, state: FSMContext) -> None:
    await _date_chosen(query, state, date.fromordinal(callback_data.v))


@router.message(StateFilter(Correction.choosing_date), F.text)
async def correction_date_typed(message: Message, state: FSMContext) -> None:
    day = parse_typed_date(message.text or "")
    if day is None:
        await message.answer("تاریخ را به شکل ۱۴۰۵/۰۷/۲۶ بنویسید یا از دکمه‌ها انتخاب کنید.")
        return
    await _date_chosen(message, state, day)


async def _date_chosen(target: CallbackQuery | Message, state: FSMContext, day: date) -> None:
    min_date, max_date = await picker_range(state)
    if not min_date <= day <= max_date:
        await reply(
            target,
            f"این تاریخ قابل انتخاب نیست. فقط از {format_date(min_date)} تا {format_date(max_date)} مجاز است.",
        )
        return
    await state.update_data(day=day.toordinal())
    await _ask_kind(target, state, day)


async def _ask_kind(target: CallbackQuery | Message, state: FSMContext, day: date) -> None:
    await state.set_state(Correction.choosing_kind)
    markup = inline(
        [
            # Soroush draws the row left to right: ورود ends up on the right.
            [button("خروج", CorrCb(a="kind", k="out").pack()), button("ورود", CorrCb(a="kind", k="in").pack())],
            step_row(CorrCb(a="back", k="date").pack()),
        ]
    )
    await reply(target, f"📝 ثبت فراموشی — {format_date(day)}\nکدام را فراموش کرده‌اید؟", markup)


@router.callback_query(StateFilter(Correction.choosing_kind), CorrCb.filter(F.a == "kind"))
async def correction_kind(query: CallbackQuery, callback_data: CorrCb, state: FSMContext) -> None:
    if callback_data.k not in ("in", "out"):
        raise ExpiredActionError()
    await state.update_data(kind=callback_data.k)
    await _ask_time(query, state)


def _time_back(data: dict) -> InlineKeyboardMarkup:
    # Manual time for today goes back to the automatic/manual choice, a forgotten entry to ورود/خروج.
    step = "punch" if data.get("origin") == "today" else "kind"
    return inline([step_row(CorrCb(a="back", k=step).pack())])


async def _ask_time(target: CallbackQuery | Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_state(Correction.typing_time)
    day = date.fromordinal(data["day"])
    which = "امروز" if data.get("origin") == "today" else "روز"
    await reply(
        target,
        f"🕘 ساعت {_kind_label(data['kind'])} {which} ({format_date(day)}) را بنویسید.\n{TIME_FORMAT_HINT}",
        _time_back(data),
    )


@router.message(StateFilter(Correction.typing_time), F.text)
async def correction_time_typed(message: Message, state: FSMContext, services: Services, user: User) -> None:
    back = _time_back(await state.get_data())
    value, problem = check_hhmm(message.text or "")
    if problem:
        await message.answer(problem, reply_markup=back)
        return
    data = await state.get_data()
    draft = _correction_input({**data, f"{data['kind']}_time": value.strftime("%H:%M")})
    try:
        # Check against the day's records now, so the person can fix the time without starting over.
        await services.attendance.validate_correction(user, draft)
    except (ValidationError, ConflictError) as exc:
        await message.answer(f"❌ {exc.user_message}\nساعت دیگری بنویسید یا «مرحله قبل» را بزنید.", reply_markup=back)
        return
    await state.update_data({"in_time": None, "out_time": None, f"{data['kind']}_time": value.strftime("%H:%M")})
    await state.set_state(Correction.confirming)
    data = await state.get_data()
    title = "مرور ثبت دستی" if data.get("origin") == "today" else "مرور ثبت فراموشی"
    text = texts.correction_review(
        KIND_BY_CODE[data["kind"]], draft.work_date, data["in_time"], data["out_time"], title=title
    )
    await message.answer(
        text, reply_markup=inline(confirm_rows(CorrCb(a="ok").pack(), CorrCb(a="back", k="time").pack()))
    )


@router.callback_query(StateFilter(Correction), CorrCb.filter(F.a == "back"))
async def correction_back(
    query: CallbackQuery, callback_data: CorrCb, state: FSMContext, services: Services, user: User
) -> None:
    data = await state.get_data()
    if callback_data.k == "punch" and "kind" in data:
        await state.clear()
        if data["kind"] == "in":
            await check_in_preview(query, services, user)
        else:
            await check_out_preview(query, services, user)
    elif callback_data.k == "date":
        await _ask_date(query, state, services)
    elif callback_data.k == "kind" and "day" in data:
        await _ask_kind(query, state, date.fromordinal(data["day"]))
    elif callback_data.k == "time" and "kind" in data:
        await _ask_time(query, state)
    else:
        raise ExpiredActionError()


def _correction_input(data: dict) -> CorrectionInput:
    def to_time(value: str | None) -> time | None:
        return time.fromisoformat(value) if value else None

    try:
        return CorrectionInput(
            correction_type=KIND_BY_CODE[data["kind"]],
            work_date=date.fromordinal(data["day"]),
            check_in_time=to_time(data.get("in_time")),
            check_out_time=to_time(data.get("out_time")),
        )
    except KeyError:
        raise ExpiredActionError() from None


@router.callback_query(StateFilter(Correction.confirming), CorrCb.filter(F.a == "ok"))
async def correction_confirm(query: CallbackQuery, state: FSMContext, services: Services, user: User) -> None:
    data = await state.get_data()
    draft = _correction_input(data)
    await state.clear()
    result = await services.attendance.submit_correction(user, draft)
    if data.get("origin") == "today" and result.status == CorrectionStatus.APPLIED:
        kind = data["kind"]
        at = result.check_in_local if kind == "in" else result.check_out_local
        calc = services.attendance.calculate(user, draft.work_date, result.record)
        await show(query, f"{texts.punch_saved(kind, at, calc)}\n✍️ ثبت دستی", back_to_menu())
        return
    if result.status == CorrectionStatus.PENDING_APPROVAL:
        text = "📨 ثبت فراموشی شما ذخیره شد و پس از تأیید مدیر اعمال می‌شود."
    else:
        text = "✅ ثبت فراموشی انجام شد و در گزارش‌ها با علامت ✍️ نمایش داده می‌شود."
    await show(query, text, back_to_menu())


# Buttons from an old or finished dialog.
@router.callback_query(CorrCb.filter())
@router.callback_query(CalCb.filter(F.p == "corr"))
@router.callback_query(TimeCb.filter(F.p.startswith("corr_")))
async def correction_stale(query: CallbackQuery) -> None:
    raise ExpiredActionError()
