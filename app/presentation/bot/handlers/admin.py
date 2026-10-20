"""Admin panel: attendance reports, leave inbox and export, users and roles, system health."""

from datetime import date, timedelta
from uuid import UUID

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import text

from app.application.presenters import attendance as att_texts
from app.application.presenters import leave as leave_texts
from app.application.services import access
from app.application.services.container import Services
from app.core.exceptions import ExpiredActionError, NotFoundError, PermissionDeniedError
from app.core.security.rbac import ROLE_TITLES_FA, Permission, RoleCode, is_admin
from app.core.utils.jalali import format_date, format_date_numeric, month_range, previous_month_range, week_range
from app.core.utils.text import to_persian_digits
from app.domain.leave.models import OPEN_LEAVE_STATUSES
from app.domain.notifications.models import NotificationType
from app.domain.users.models import User
from app.infrastructure.excel.reports import build_attendance_workbook, build_leave_workbook, report_filename
from app.infrastructure.integrations.messenger import to_markup
from app.presentation.api.health import check_redis
from app.presentation.bot.callbacks.data import AdminCb, CalCb, NavCb, UserAdminCb
from app.presentation.bot.handlers.common import picker_range, query_message, send_file, show, start_picker
from app.presentation.bot.handlers.leave import approval_buttons
from app.presentation.bot.keyboards.common import back_row, button, chunk, inline
from app.presentation.bot.keyboards.pickers import calendar_for
from app.presentation.bot.middlewares.filters import IsRegistered
from app.presentation.bot.modules import BTN_ADMIN
from app.presentation.bot.states.flows import ReportRange

router = Router(name="admin")
router.message.filter(IsRegistered())
router.callback_query.filter(IsRegistered())

PAGE_SIZE = 8
TITLE = "🛠 پنل مدیریت"


def panel_markup(user: User):
    rows = []
    if access.can(user, Permission.ATTENDANCE_REPORT_ALL):
        rows.append(
            [
                button("📊 حضور امروز", AdminCb(a="att", v="today").pack()),
                button("📊 هفته جاری", AdminCb(a="att", v="week").pack()),
            ]
        )
        rows.append(
            [
                button("📊 ماه جاری", AdminCb(a="att", v="month").pack()),
                button("📊 ماه قبل", AdminCb(a="att", v="prev").pack()),
            ]
        )
        rows.append(
            [
                button("📅 بازه‌ی دلخواه", AdminCb(a="att_range").pack()),
                button("👤 گزارش یک کارمند", AdminCb(a="att_emp", v="0").pack()),
            ]
        )
    if access.can(user, Permission.LEAVE_APPROVE_STEP_1) or access.can(user, Permission.LEAVE_APPROVE_STEP_2):
        rows.append([button("📥 کارتابل مرخصی", AdminCb(a="lv_inbox").pack())])
    if access.can(user, Permission.LEAVE_REPORT_ALL):
        rows.append(
            [
                button("📤 مرخصی‌های ماه جاری", AdminCb(a="lv_xls", v="month").pack()),
                button("📤 مرخصی‌های باز", AdminCb(a="lv_xls", v="open").pack()),
            ]
        )
    if access.can(user, Permission.USERS_VIEW):
        rows.append([button("👥 کاربران و نقش‌ها", UserAdminCb(a="list", p=0).pack())])
    if access.can(user, Permission.SYSTEM_HEALTH):
        rows.append([button("🩺 وضعیت سیستم", AdminCb(a="health").pack())])
    rows.append(back_row())
    return inline(rows)


def _require_admin(user: User) -> None:
    if not is_admin(user.roles):
        raise PermissionDeniedError()


@router.message(F.text == BTN_ADMIN)
async def open_panel(message: Message, state: FSMContext, user: User) -> None:
    _require_admin(user)
    await state.clear()
    await message.answer(TITLE, reply_markup=panel_markup(user))


@router.callback_query(NavCb.filter(F.to == "admin"))
async def open_panel_button(query: CallbackQuery, state: FSMContext, user: User) -> None:
    _require_admin(user)
    await state.clear()
    await show(query, TITLE, panel_markup(user))


# --- attendance reports --------------------------------------------------------------------------


async def _send_attendance(
    query: CallbackQuery,
    services: Services,
    user: User,
    start: date,
    end: date,
    title: str,
    user_id: UUID | None = None,
) -> None:
    reports = await services.attendance.team_reports(user, start, end, user_id)
    await query.answer("در حال آماده‌سازی گزارش…")
    message = query_message(query)
    if user_id is not None and reports:
        await message.answer(att_texts.employee_report(reports[0], title))
    else:
        summary = services.attendance.team_summary(reports, start, end)
        await message.answer(att_texts.team_summary(summary, title))
    prefix = "attendance_employee" if user_id else "attendance_all"
    await send_file(message, build_attendance_workbook(reports), report_filename(prefix, start, end))


def _period(services: Services, code: str) -> tuple[date, date, str]:
    today = services.time.today()
    if code == "today":
        return today, today, "گزارش حضور امروز"
    if code == "week":
        return *week_range(today), "گزارش حضور هفته‌ی جاری"
    if code == "month":
        return *month_range(today), "گزارش حضور ماه جاری"
    if code == "prev":
        return *previous_month_range(today), "گزارش حضور ماه قبل"
    raise ExpiredActionError()


@router.callback_query(AdminCb.filter(F.a == "att"))
async def attendance_period(query: CallbackQuery, callback_data: AdminCb, services: Services, user: User) -> None:
    start, end, title = _period(services, callback_data.v)
    await _send_attendance(query, services, user, start, end, title)


@router.callback_query(AdminCb.filter(F.a == "att_range"))
async def attendance_range_start(
    query: CallbackQuery, state: FSMContext, services: Services, user: User, emp: str = ""
) -> None:
    access.require(user, Permission.ATTENDANCE_REPORT_ALL)
    today = services.time.today()
    await state.set_state(ReportRange.choosing)
    await state.update_data(emp=emp)
    await start_picker(state, today - timedelta(days=400), today)
    await show(
        query,
        "📅 تاریخ شروع گزارش را انتخاب کنید:",
        calendar_for("rep_s", today.replace(day=1), today - timedelta(days=400), today),
    )


@router.callback_query(StateFilter(ReportRange.choosing), CalCb.filter((F.a == "d") & F.p.in_({"rep_s", "rep_e"})))
async def attendance_range_pick(
    query: CallbackQuery, callback_data: CalCb, state: FSMContext, services: Services, user: User
) -> None:
    min_date, max_date = await picker_range(state)
    day = date.fromordinal(callback_data.v)
    if not min_date <= day <= max_date:
        raise ExpiredActionError()
    if callback_data.p == "rep_s":
        await state.update_data(rep_start=callback_data.v)
        await start_picker(state, day, max_date)
        await show(
            query,
            f"شروع: {format_date(day)}\n📅 تاریخ پایان گزارش را انتخاب کنید:",
            calendar_for("rep_e", day, day, max_date),
        )
        return
    data = await state.get_data()
    await state.clear()
    start = date.fromordinal(data["rep_start"])
    emp = UUID(hex=data["emp"]) if data.get("emp") else None
    title = f"گزارش حضور {format_date_numeric(start)} تا {format_date_numeric(day)}"
    await _send_attendance(query, services, user, start, day, title, emp)


@router.callback_query(AdminCb.filter(F.a == "att_emp"))
async def attendance_employee_list(
    query: CallbackQuery, callback_data: AdminCb, services: Services, user: User
) -> None:
    access.require(user, Permission.ATTENDANCE_REPORT_ALL)
    page = int(callback_data.v or 0)
    users, total = await services.users.list_page(user, page, PAGE_SIZE)
    rows = [[button(u.full_name, AdminCb(a="att_emp_sel", v=u.id.hex).pack())] for u in users]
    nav = []
    if page > 0:
        nav.append(button("◀️ قبلی", AdminCb(a="att_emp", v=str(page - 1)).pack()))
    if (page + 1) * PAGE_SIZE < total:
        nav.append(button("بعدی ▶️", AdminCb(a="att_emp", v=str(page + 1)).pack()))
    if nav:
        rows.append(nav)
    rows.append(back_row("admin"))
    await show(query, "👤 کارمند موردنظر را انتخاب کنید:", inline(rows))


@router.callback_query(AdminCb.filter(F.a == "att_emp_sel"))
async def attendance_employee_period(
    query: CallbackQuery, callback_data: AdminCb, services: Services, user: User
) -> None:
    target = await services.users.get(UUID(hex=callback_data.v))
    hex_id = target.id.hex
    rows = [
        [
            button("هفته‌ی جاری", AdminCb(a="att_emp_p", v=f"{hex_id}.week").pack()),
            button("ماه جاری", AdminCb(a="att_emp_p", v=f"{hex_id}.month").pack()),
        ],
        [
            button("ماه قبل", AdminCb(a="att_emp_p", v=f"{hex_id}.prev").pack()),
            button("بازه‌ی دلخواه", AdminCb(a="att_emp_p", v=f"{hex_id}.range").pack()),
        ],
        back_row("admin"),
    ]
    await show(query, f"👤 {target.full_name}\nبازه‌ی گزارش را انتخاب کنید:", inline(rows))


@router.callback_query(AdminCb.filter(F.a == "att_emp_p"))
async def attendance_employee_report(
    query: CallbackQuery, callback_data: AdminCb, state: FSMContext, services: Services, user: User
) -> None:
    hex_id, code = callback_data.v.split(".")
    if code == "range":
        await attendance_range_start(query, state, services, user, emp=hex_id)
        return
    start, end, title = _period(services, code)
    target = await services.users.get(UUID(hex=hex_id))
    await _send_attendance(query, services, user, start, end, f"{title} — {target.full_name}", target.id)


# --- leave inbox and export -------------------------------------------------------------------------


@router.callback_query(AdminCb.filter(F.a == "lv_inbox"))
async def leave_inbox(query: CallbackQuery, services: Services, user: User) -> None:
    requests = await services.leave.pending_for(user)
    if not requests:
        await show(query, "📥 درخواستی در انتظار بررسی شما نیست.", inline([back_row("admin")]))
        return
    await show(query, f"📥 {to_persian_digits(len(requests))} درخواست در انتظار بررسی:", inline([back_row("admin")]))
    employees = await services.leave.users_by_id({r.user_id for r in requests})
    message = query_message(query)
    for request in requests[:15]:
        step = await services.leave.current_step(request.id)
        if step is None:
            continue
        title = services.leave.workflow.step(step.step_order).title_fa
        await message.answer(
            leave_texts.request_details(request, employees.get(request.user_id), title),
            reply_markup=to_markup(approval_buttons(request, step.step_order)),
        )


@router.callback_query(AdminCb.filter(F.a == "lv_xls"))
async def leave_export(query: CallbackQuery, callback_data: AdminCb, services: Services, user: User) -> None:
    today = services.time.today()
    if callback_data.v == "open":
        start, end, statuses, label = None, None, set(OPEN_LEAVE_STATUSES), "open"
    else:
        start, end = month_range(today)
        statuses, label = None, "month"
    requests = await services.leave.list_for_report(user, start, end, statuses)
    users = await services.leave.users_by_id({r.user_id for r in requests})
    await query.answer("در حال آماده‌سازی فایل…")
    message = query_message(query)
    await message.answer(f"📤 تعداد درخواست‌ها: {to_persian_digits(len(requests))}")
    s, e = (start, end) if start and end else (today, today)
    await send_file(
        message, build_leave_workbook(requests, users, services.time.to_local), report_filename(f"leave_{label}", s, e)
    )


# --- users and roles ----------------------------------------------------------------------------------


@router.callback_query(UserAdminCb.filter(F.a == "list"))
async def users_list(query: CallbackQuery, callback_data: UserAdminCb, services: Services, user: User) -> None:
    users, total = await services.users.list_page(user, callback_data.p, PAGE_SIZE)
    rows = [
        [button(f"{'✅' if u.is_active else '⛔'} {u.full_name}", UserAdminCb(a="view", u=u.id.hex).pack())]
        for u in users
    ]
    nav = []
    if callback_data.p > 0:
        nav.append(button("◀️ قبلی", UserAdminCb(a="list", p=callback_data.p - 1).pack()))
    if (callback_data.p + 1) * PAGE_SIZE < total:
        nav.append(button("بعدی ▶️", UserAdminCb(a="list", p=callback_data.p + 1).pack()))
    if nav:
        rows.append(nav)
    rows.append(back_row("admin"))
    await show(query, f"👥 کاربران ({to_persian_digits(total)} نفر)", inline(rows))


def _user_card(target: User, services: Services) -> str:
    roles = "، ".join(ROLE_TITLES_FA[r] for r in sorted(target.roles)) or "—"
    registered = format_date(services.time.to_local(target.registered_at).date()) if target.registered_at else "—"
    return (
        f"👤 {target.full_name}\n🆔 شناسه: {target.messenger_user_id}\n"
        f"نام کاربری: {'@' + target.username if target.username else '—'}\n"
        f"وضعیت: {'فعال ✅' if target.is_active else 'غیرفعال ⛔'}\nنقش‌ها: {roles}\nثبت‌نام: {registered}"
    )


def _user_markup(target: User, actor: User):
    rows = []
    if access.can(actor, Permission.USERS_MANAGE_ROLES):
        role_buttons = []
        for role in RoleCode:
            has = role in target.roles
            role_buttons.append(
                button(
                    f"{'✅' if has else '➕'} {ROLE_TITLES_FA[role]}",
                    UserAdminCb(a="role", u=target.id.hex, r=role.value, p=0 if has else 1).pack(),
                )
            )
        rows.extend(chunk(role_buttons, 2))
        rows.append(
            [
                button(
                    "⛔ غیرفعال کردن" if target.is_active else "✅ فعال کردن",
                    UserAdminCb(a="active", u=target.id.hex, p=0 if target.is_active else 1).pack(),
                )
            ]
        )
    rows.append([button("🔙 فهرست کاربران", UserAdminCb(a="list", p=0).pack())])
    return inline(rows)


@router.callback_query(UserAdminCb.filter(F.a == "view"))
async def user_view(query: CallbackQuery, callback_data: UserAdminCb, services: Services, user: User) -> None:
    access.require(user, Permission.USERS_VIEW)
    target = await services.users.get(UUID(hex=callback_data.u))
    await show(query, _user_card(target, services), _user_markup(target, user))


@router.callback_query(UserAdminCb.filter(F.a == "role"))
async def user_role(query: CallbackQuery, callback_data: UserAdminCb, services: Services, user: User) -> None:
    target = await services.users.set_role(
        user, UUID(hex=callback_data.u), RoleCode(callback_data.r), bool(callback_data.p)
    )
    await show(query, _user_card(target, services) + "\n\n✔️ نقش‌ها به‌روز شد.", _user_markup(target, user))
    if target.id != user.id:
        change = "اضافه شد" if callback_data.p else "حذف شد"
        await services.notifications.notify_user(
            target,
            f"ℹ️ نقش «{ROLE_TITLES_FA[RoleCode(callback_data.r)]}» برای شما {change}. "
            "برای به‌روزرسانی منو /start را بزنید.",
            NotificationType.GENERIC,
        )


@router.callback_query(UserAdminCb.filter(F.a == "active"))
async def user_active(query: CallbackQuery, callback_data: UserAdminCb, services: Services, user: User) -> None:
    target = await services.users.set_active(user, UUID(hex=callback_data.u), bool(callback_data.p))
    await show(query, _user_card(target, services), _user_markup(target, user))


@router.message(Command("user"))
async def user_lookup(message: Message, command: CommandObject, services: Services, user: User) -> None:
    access.require(user, Permission.USERS_VIEW)
    arg = (command.args or "").strip()
    if not arg.isdigit():
        await message.answer("استفاده: /user <شناسه‌ی عددی کاربر>")
        return
    target = await services.users.get_by_messenger_id(int(arg))
    if target is None:
        raise NotFoundError("کاربری با این شناسه ثبت‌نام نکرده است.")
    await message.answer(_user_card(target, services), reply_markup=_user_markup(target, user))


# --- health -----------------------------------------------------------------------------------------


@router.callback_query(AdminCb.filter(F.a == "health"))
@router.message(Command("health"))
async def health(event: CallbackQuery | Message, services: Services, user: User) -> None:
    access.require(user, Permission.SYSTEM_HEALTH)
    db_ok = True
    try:
        await services.session.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    redis_ok = await check_redis()
    lines = [
        "🩺 وضعیت سیستم",
        f"پایگاه داده: {'✅' if db_ok else '❌'}",
        f"Redis: {'✅' if redis_ok else '❌'}",
        f"زمان سرور (تهران): {format_date(services.time.today())} {services.time.now_local():%H:%M}",
    ]
    if isinstance(event, CallbackQuery):
        await show(event, "\n".join(lines), inline([back_row("admin")]))
    else:
        await event.answer("\n".join(lines))
