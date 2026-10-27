"""Attendance module UI. Every rule lives in AttendanceService; this file only drives the dialog."""

from datetime import UTC, date, datetime, time

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from app.application.dto.attendance import CorrectionInput, EditInput
from app.application.presenters import attendance as texts
from app.application.services.container import Services
from app.core.exceptions import ConflictError, ExpiredActionError, ValidationError
from app.core.utils.jalali import format_date, format_time
from app.core.utils.text import to_persian_digits
from app.domain.attendance.models import CorrectionStatus, CorrectionType
from app.domain.users.models import User
from app.infrastructure.excel.reports import build_attendance_workbook, report_filename
from app.infrastructure.excel.timesheet import build_timesheet_workbook
from app.presentation.bot.callbacks.data import OPEN_ATTENDANCE, AttCb, CalCb, CorrCb, KeyCb, NavCb, TimeCb
from app.presentation.bot.handlers.common import (
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
from app.presentation.bot.keyboards.pickers import (
    TYPE_DATE_HINT,
    calendar_for,
    keypad_digits,
    keypad_display,
    time_keypad,
)
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
            [
                button("✏️ ویرایش ورود/خروج", AttCb(a="edit").pack()),
                button("📝 ثبت فراموشی", AttCb(a="forgot").pack()),
            ],
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
    """Manual time for today: the same typed-time step as a forgotten entry, but saved as today's
    manual time (✍️), not as a forgotten entry (📝)."""
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
    month = await services.attendance.my_month(user)
    markup = inline([[button("📥 دریافت فایل اکسل", AttCb(a="week_xls").pack())], back_row("att")])
    await show(query, texts.employee_report(report, month=month), markup)


@router.callback_query(AttCb.filter(F.a == "month"))
async def monthly_report(query: CallbackQuery, services: Services, user: User) -> None:
    report = await services.attendance.my_month(user)
    markup = inline([[button("📥 دریافت فایل اکسل", AttCb(a="month_xls").pack())], back_row("att")])
    # A whole month of day lines is too long for one message; the Excel file has the details.
    await show(query, texts.monthly_report(report), markup)


@router.callback_query(AttCb.filter(F.a.in_({"week_xls", "month_xls"})))
async def my_excel(query: CallbackQuery, callback_data: AttCb, services: Services, user: User) -> None:
    if callback_data.a == "month_xls":
        report = await services.attendance.my_month(user)
    else:
        report = await services.attendance.my_week(user)
    await query.answer("در حال آماده‌سازی فایل…")
    if callback_data.a == "month_xls" and not report.hourly:
        rules, holidays = services.attendance.sheet_rules, services.attendance.holidays
        content = build_timesheet_workbook([report], rules, holidays)
        filename = report_filename("timesheet_me", report.start, report.end)
    else:
        content = build_attendance_workbook([report])
        filename = report_filename("attendance_me", report.start, report.end)
    await send_file(query_message(query), content, filename)


# --- forgotten entry, manual time for today and editing a registered time ----------------------
# One dialog, three origins (FSM "origin"):
#   forgot: date → ورود/خروج → time → confirm   (earlier days with a missing time, 📝)
#   today:  ثبت ورود/خروج امروز → دستی → time → confirm   (✍️)
#   edit:   date → ورود/خروج (registered ones) → new time → confirm   (✏️)
# The time is picked digit by digit on a keypad or typed as HH:MM. Every step has "previous step".


def _kind_label(kind: str) -> str:
    return "ورود" if kind == "in" else "خروج"


@router.callback_query(AttCb.filter(F.a.in_({"forgot", "edit"})))
async def correction_start(query: CallbackQuery, callback_data: AttCb, state: FSMContext, services: Services) -> None:
    await state.clear()
    await state.update_data(origin=callback_data.a)
    await _ask_date(query, state, services)


async def _ask_date(query: CallbackQuery, state: FSMContext, services: Services) -> None:
    editing = (await state.get_data()).get("origin") == "edit"
    if editing:
        min_date, max_date = services.attendance.edit_date_window()
        text = f"✏️ ویرایش ورود/خروج\n📅 تاریخ روزی را که می‌خواهید ویرایش کنید انتخاب کنید.\n{TYPE_DATE_HINT}"
    else:
        min_date, max_date = services.attendance.correction_date_window()
        text = f"📝 ثبت فراموشی\n📅 تاریخ روزی را که ثبت نشده انتخاب کنید.\n{TYPE_DATE_HINT}"
    back = NavCb(to="att").pack()
    await state.set_state(Correction.choosing_date)
    await start_picker(state, min_date, max_date, back=back)
    await show(query, text, calendar_for("corr", services.time.today(), min_date, max_date, back=back))


@router.callback_query(StateFilter(Correction.choosing_date), CalCb.filter((F.p == "corr") & (F.a == "d")))
async def correction_date(
    query: CallbackQuery, callback_data: CalCb, state: FSMContext, services: Services, user: User
) -> None:
    await _date_chosen(query, state, date.fromordinal(callback_data.v), services, user)


@router.message(StateFilter(Correction.choosing_date), F.text)
async def correction_date_typed(message: Message, state: FSMContext, services: Services, user: User) -> None:
    day = parse_typed_date(message.text or "")
    if day is None:
        await message.answer("تاریخ را به شکل ۱۴۰۵/۰۷/۲۶ بنویسید یا از دکمه‌ها انتخاب کنید.")
        return
    await _date_chosen(message, state, day, services, user)


async def _date_chosen(
    target: CallbackQuery | Message, state: FSMContext, day: date, services: Services, user: User
) -> None:
    min_date, max_date = await picker_range(state)
    if not min_date <= day <= max_date:
        await reply(
            target,
            f"این تاریخ قابل انتخاب نیست. فقط از {format_date(min_date)} تا {format_date(max_date)} مجاز است.",
        )
        return
    await state.update_data(day=day.toordinal())
    await _ask_kind(target, state, day, services, user)


async def _ask_kind(
    target: CallbackQuery | Message, state: FSMContext, day: date, services: Services, user: User
) -> None:
    back = step_row(CorrCb(a="back", k="date").pack())
    if (await state.get_data()).get("origin") != "edit":
        await state.set_state(Correction.choosing_kind)
        markup = inline(
            [
                # Soroush draws the row left to right: ورود ends up on the right.
                [button("خروج", CorrCb(a="kind", k="out").pack()), button("ورود", CorrCb(a="kind", k="in").pack())],
                back,
            ]
        )
        await reply(target, f"📝 ثبت فراموشی — {format_date(day)}\nکدام را فراموش کرده‌اید؟", markup)
        return
    record = await services.attendance.record_for(user, day)
    times = {
        "in": record.check_in_at if record else None,
        "out": record.check_out_at if record else None,
    }
    if not any(times.values()):
        await reply(
            target,
            f"برای {format_date(day)} ورود یا خروجی ثبت نشده که ویرایش شود.\n"
            "روز دیگری انتخاب کنید یا از «ثبت فراموشی» استفاده کنید.",
            inline([back]),
        )
        return
    await state.set_state(Correction.choosing_kind)
    shown = {k: format_time(services.time.to_local(v)) if v else "ثبت نشده" for k, v in times.items()}
    buttons = [
        button(f"{_kind_label(k)} ({shown[k]})", CorrCb(a="kind", k=k).pack()) for k in ("out", "in") if times[k]
    ]
    await reply(
        target,
        f"✏️ ویرایش — {format_date(day)}\nورود: {shown['in']} | خروج: {shown['out']}\nکدام را ویرایش می‌کنید؟",
        inline([buttons, back]),
    )


@router.callback_query(StateFilter(Correction.choosing_kind), CorrCb.filter(F.a == "kind"))
async def correction_kind(query: CallbackQuery, callback_data: CorrCb, state: FSMContext) -> None:
    if callback_data.k not in ("in", "out"):
        raise ExpiredActionError()
    await state.update_data(kind=callback_data.k)
    await _ask_time(query, state)


def _time_back(data: dict) -> str:
    # Manual time for today goes back to the automatic/manual choice, the others to ورود/خروج.
    return CorrCb(a="back", k="punch" if data.get("origin") == "today" else "kind").pack()


def _time_prompt(data: dict, typed: str, problem: str | None = None) -> str:
    day = date.fromordinal(data["day"])
    origin = data.get("origin")
    if origin == "edit":
        head = f"🕘 ساعت جدید {_kind_label(data['kind'])} روز {format_date(day)}"
    else:
        which = "امروز" if origin == "today" else "روز"
        head = f"🕘 ساعت {_kind_label(data['kind'])} {which} ({format_date(day)})"
    text = (
        f"{head}\n\n⌨️ {keypad_display(typed)}\n\n"
        "رقم‌ها را یکی‌یکی از دکمه‌ها انتخاب کنید (۲۴ ساعته)، یا ساعت را بنویسید؛ مثل ۰۸:۴۵ یا ۱۷:۳۰."
    )
    return f"{problem}\n\n{text}" if problem else text


async def _ask_time(target: CallbackQuery | Message, state: FSMContext, problem: str | None = None) -> None:
    data = await state.get_data()
    await state.set_state(Correction.typing_time)
    await reply(target, _time_prompt(data, "", problem), time_keypad("corr", "", _time_back(data)))


@router.callback_query(StateFilter(Correction.typing_time), KeyCb.filter(F.p == "corr"))
async def correction_keypad(
    query: CallbackQuery, callback_data: KeyCb, state: FSMContext, services: Services, user: User
) -> None:
    typed = callback_data.v
    if callback_data.a == "del":
        typed = typed[:-1]
    elif callback_data.d in keypad_digits(typed):
        typed += str(callback_data.d)
    else:
        raise ExpiredActionError()
    if len(typed) < 4:
        data = await state.get_data()
        await show(query, _time_prompt(data, typed), time_keypad("corr", typed, _time_back(data)))
        return
    await _time_entered(query, state, services, user, time(int(typed[:2]), int(typed[2:])))


@router.message(StateFilter(Correction.typing_time), F.text)
async def correction_time_typed(message: Message, state: FSMContext, services: Services, user: User) -> None:
    value, problem = check_hhmm(message.text or "")
    if problem:
        await _ask_time(message, state, problem)
        return
    await _time_entered(message, state, services, user, value)


async def _time_entered(
    target: CallbackQuery | Message, state: FSMContext, services: Services, user: User, value: time
) -> None:
    data = await state.get_data()
    kind, day = data["kind"], date.fromordinal(data["day"])
    hhmm = value.strftime("%H:%M")
    try:
        # Check against the day's records now, so the person can fix the time without starting over.
        if data.get("origin") == "edit":
            previous, _ = await services.attendance.validate_edit(user, EditInput(day, kind, value))
        else:
            await services.attendance.validate_correction(user, _correction_input({**data, f"{kind}_time": hhmm}))
    except (ValidationError, ConflictError) as exc:
        await _ask_time(target, state, f"❌ {exc.user_message}\nساعت دیگری وارد کنید یا «مرحله قبل» را بزنید.")
        return
    await state.update_data({"in_time": None, "out_time": None, f"{kind}_time": hhmm})
    await state.set_state(Correction.confirming)
    if data.get("origin") == "edit":
        text = (
            f"✏️ مرور ویرایش\nتاریخ: {format_date(day)}\n"
            f"{_kind_label(kind)}: {format_time(services.time.to_local(previous))} ← {to_persian_digits(hhmm)}\n\n"
            "آیا ویرایش ثبت شود؟"
        )
    else:
        title = "مرور ثبت دستی" if data.get("origin") == "today" else "مرور ثبت فراموشی"
        in_time, out_time = (hhmm, None) if kind == "in" else (None, hhmm)
        text = texts.correction_review(KIND_BY_CODE[kind], day, in_time, out_time, title=title)
    markup = inline(confirm_rows(CorrCb(a="ok").pack(), CorrCb(a="back", k="time").pack()))
    await reply(target, text, markup)


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
        await _ask_kind(query, state, date.fromordinal(data["day"]), services, user)
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
            same_day=data.get("origin") == "today",
        )
    except KeyError:
        raise ExpiredActionError() from None


@router.callback_query(StateFilter(Correction.confirming), CorrCb.filter(F.a == "ok"))
async def correction_confirm(query: CallbackQuery, state: FSMContext, services: Services, user: User) -> None:
    data = await state.get_data()
    await state.clear()
    if data.get("origin") == "edit":
        kind = data.get("kind")
        value = data.get(f"{kind}_time")
        if kind not in ("in", "out") or not value or "day" not in data:
            raise ExpiredActionError()
        day = date.fromordinal(data["day"])
        result = await services.attendance.edit_entry(user, EditInput(day, kind, time.fromisoformat(value)))
        if result.status == CorrectionStatus.PENDING_APPROVAL:
            text = "📨 ویرایش شما ذخیره شد و پس از تأیید مدیر اعمال می‌شود."
        else:
            text = (
                f"✅ {_kind_label(kind)} روز {format_date(day)} به ساعت {to_persian_digits(value)} ویرایش شد."
                "\nدر گزارش‌ها با علامت ✏️ نمایش داده می‌شود."
            )
        await show(query, text, back_to_menu())
        return
    draft = _correction_input(data)
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
        text = "✅ ثبت فراموشی انجام شد و در گزارش‌ها با علامت 📝 نمایش داده می‌شود."
    await show(query, text, back_to_menu())


# Buttons from an old or finished dialog.
@router.callback_query(CorrCb.filter())
@router.callback_query(CalCb.filter(F.p == "corr"))
@router.callback_query(TimeCb.filter(F.p.startswith("corr_")))
@router.callback_query(KeyCb.filter(F.p == "corr"))
async def correction_stale(query: CallbackQuery) -> None:
    raise ExpiredActionError()
