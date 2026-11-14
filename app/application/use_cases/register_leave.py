"""An admin registers a leave directly into a full-time employee's Excel file.

The employee's uploaded workbook is the only record: the cells of that day's row are overwritten as
`domain/leave/excel_rules.py` defines, and the change is kept in the audit log. To cancel or correct a
leave, the admin downloads the file, clears the cells and uploads it again.

    check (admin, full-time, date, times) → read the day's row (E/F/G and the leave columns)
    → work out the case and the cells → write them into a new version of the file → audit → notify
"""

import asyncio
from dataclasses import dataclass
from datetime import date, time, timedelta
from uuid import UUID

from app.application.services import access
from app.application.services.container import Services
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.security.rbac import Permission
from app.core.utils.jalali import format_date, to_jalali
from app.core.utils.text import to_persian_digits
from app.domain.attendance.models import AttendanceWorkbook
from app.domain.audit.models import AuditAction
from app.domain.leave.excel_rules import (
    ALLOWED_EXIT,
    EDGE_LEAVE,
    FLOAT_END,
    FLOAT_START,
    MIDDAY_LEAVE,
    DayTimes,
    LeaveCells,
    LeaveRuleError,
    daily_leave_cells,
    hourly_leave_cells,
)
from app.domain.leave.models import LeavePayType, LeaveUnit
from app.domain.notifications.models import NotificationType
from app.domain.users.models import User
from app.infrastructure.excel.workbook import (
    WorkbookError,
    cell_minutes,
    format_duration,
    normalize_label,
    read_day_cells,
    write_cells,
)
from app.infrastructure.repositories.users import UserRepository

PAY_TITLES = {LeavePayType.PAID: "با حقوق", LeavePayType.UNPAID: "بدون حقوق"}
UNIT_TITLES = {LeaveUnit.DAILY: "روزانه", LeaveUnit.HOURLY: "ساعتی"}
CANCEL_HINT = (
    "برای لغو یا اصلاح: فایل را از «📊 فایل اکسل حضور» دریافت کنید، خانه‌ها را پاک کنید و دوباره بارگذاری کنید."
)


@dataclass(frozen=True)
class LeaveInput:
    employee_id: UUID
    pay_type: LeavePayType
    unit: LeaveUnit
    day: date
    start: time | None = None  # x, hourly only
    end: time | None = None  # y, hourly only


@dataclass(frozen=True)
class LeavePlan:
    employee: User
    workbook: AttendanceWorkbook
    data: LeaveInput
    cells: LeaveCells


def leave_date_window(services: Services) -> tuple[date, date]:
    """Leave can be registered from `leave.max_past_days` ago to `leave.max_future_days` ahead."""
    today = services.time.today()
    policy = services.policy.leave
    return today - timedelta(days=policy.max_past_days), today + timedelta(days=policy.max_future_days)


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


async def full_time_employees(services: Services, actor: User) -> list[User]:
    """Active employees who are not hourly staff: the only ones leave can be registered for."""
    access.require(actor, Permission.LEAVE_REGISTER)
    return [u for u in await UserRepository(services.session).list_active_employees() if not u.is_hourly]


def _cells_for(data: LeaveInput, row: dict[str, object], sheet_hint: str) -> LeaveCells:
    if row.get(normalize_label(MIDDAY_LEAVE)) or row.get(normalize_label(EDGE_LEAVE)):
        raise ConflictError(f"برای این روز در فایل اکسل قبلاً مرخصی ثبت شده است.\n{CANCEL_HINT}")
    times = [cell_minutes(row.get(normalize_label(h))) for h in (FLOAT_START, FLOAT_END, ALLOWED_EXIT)]
    if any(t is None for t in times):
        raise ValidationError(
            f"ساعت‌های «{FLOAT_START}»، «{FLOAT_END}» و «{ALLOWED_EXIT}» این روز در {sheet_hint} خوانده نشد."
        )
    day = DayTimes(*times)  # type: ignore[arg-type]
    try:
        if data.unit == LeaveUnit.DAILY:
            cells = daily_leave_cells(day)
        else:
            if data.start is None or data.end is None:
                raise ValidationError("ساعت شروع و پایان مرخصی وارد نشده است.")
            cells = hourly_leave_cells(_minutes(data.start), _minutes(data.end), day)
    except LeaveRuleError as exc:
        raise ValidationError(str(exc)) from exc
    missing = [h for h in cells.cells if normalize_label(h) not in row]
    if missing:
        raise ValidationError(f"{sheet_hint} ستون «{missing[0]}» را ندارد؛ این مرخصی در آن قابل ثبت نیست.")
    return cells


async def plan_leave(services: Services, actor: User, data: LeaveInput) -> LeavePlan:
    """Every check, without changing anything. Raises a Persian AppError."""
    access.require(actor, Permission.LEAVE_REGISTER)
    employee = await UserRepository(services.session).get(data.employee_id)
    if employee is None or not employee.is_active:
        raise NotFoundError("کارمند پیدا نشد یا غیرفعال است.")
    if employee.is_hourly:
        raise ValidationError("ثبت مرخصی فقط برای کارکنان تمام‌وقت است.")
    earliest, latest = leave_date_window(services)
    if not earliest <= data.day <= latest:
        raise ValidationError(f"تاریخ مرخصی باید بین {format_date(earliest)} و {format_date(latest)} باشد.")
    workbook = await services.workbooks.for_user(employee.id)
    if workbook is None:
        raise ValidationError(
            f"{employee.full_name} فایل اکسل حضور ندارد. اول از کارت او «📊 فایل اکسل حضور» را تعیین کنید."
        )
    content = await asyncio.to_thread(services.workbook_store.read, workbook.id, workbook.version)
    jalali = to_jalali(data.day)
    try:
        row = await asyncio.to_thread(read_day_cells, content, jalali.month, jalali.day)
    except WorkbookError as exc:
        raise ValidationError(str(exc)) from exc
    cells = _cells_for(data, row, f"فایل اکسل {employee.full_name}")
    return LeavePlan(employee, workbook, data, cells)


async def register_leave(services: Services, actor: User, data: LeaveInput) -> LeavePlan:
    """Check again and write the cells into a new version of the employee's file."""
    plan = await plan_leave(services, actor, data)
    # Lock the file's row: a check-in being written by the worker waits, so nothing is lost.
    workbook = await services.workbooks.repo.for_user(plan.employee.id, for_update=True)
    if workbook is None:
        raise ValidationError("فایل اکسل این کارمند همین حالا تغییر کرد. دوباره امتحان کنید.")
    content = await asyncio.to_thread(services.workbook_store.read, workbook.id, workbook.version)
    jalali = to_jalali(data.day)
    try:
        row = await asyncio.to_thread(read_day_cells, content, jalali.month, jalali.day)
        cells = _cells_for(data, row, f"فایل اکسل {plan.employee.full_name}")
        content = await asyncio.to_thread(write_cells, content, jalali.month, jalali.day, cells.cells, excel_note(data))
    except WorkbookError as exc:
        await services.session.rollback()
        raise ValidationError(str(exc)) from exc
    await asyncio.to_thread(services.workbook_store.save, workbook.id, workbook.version + 1, content)
    workbook.version += 1
    services.workbooks.audit.record(
        AuditAction.LEAVE_REGISTERED_BY_ADMIN,
        "user",
        plan.employee.id,
        actor.id,
        {
            "workbook_id": str(workbook.id),
            "version": workbook.version,
            "date": data.day.isoformat(),
            "pay_type": data.pay_type.value,
            "unit": data.unit.value,
            "start": data.start.isoformat(timespec="minutes") if data.start else None,
            "end": data.end.isoformat(timespec="minutes") if data.end else None,
            "case": cells.scenario,
            "cells": {header: format_duration(m) for header, m in cells.cells.items()},
        },
    )
    await services.session.commit()
    plan = LeavePlan(plan.employee, workbook, data, cells)
    await services.notifications.notify_user(plan.employee, employee_notice(plan), NotificationType.GENERIC)
    return plan


def describe(plan: LeavePlan) -> str:
    """What will be (or was) registered, and which cells change."""
    d = plan.data
    when = format_date(d.day)
    if d.unit == LeaveUnit.HOURLY and d.start and d.end:
        when += f" از {d.start:%H:%M} تا {d.end:%H:%M}"
    lines = [
        f"👤 {plan.employee.full_name}",
        f"📝 مرخصی {UNIT_TITLES[d.unit]} {PAY_TITLES[d.pay_type]}",
        f"📅 {when}",
        f"🔎 حالت: {plan.cells.title}",
        f"📄 فایل: {plan.workbook.title}",
        "خانه‌هایی که در ردیف این روز نوشته می‌شود:",
    ]
    lines += [f"▫️ {header}: {format_duration(m)}" for header, m in plan.cells.cells.items()]
    return "\n".join(lines)


def excel_note(data: LeaveInput) -> str:
    """What is added to the day's «توضیحات», e.g. «مرخصی ساعتی با حقوق ۰۸:۰۰ تا ۱۰:۳۰»."""
    text = f"مرخصی {UNIT_TITLES[data.unit]} {PAY_TITLES[data.pay_type]}"
    if data.unit == LeaveUnit.HOURLY and data.start and data.end:
        text += f" {data.start:%H:%M} تا {data.end:%H:%M}"
    return to_persian_digits(text)


def employee_notice(plan: LeavePlan) -> str:
    d = plan.data
    when = format_date(d.day)
    if d.unit == LeaveUnit.HOURLY and d.start and d.end:
        when += f" از ساعت {d.start:%H:%M} تا {d.end:%H:%M}"
    return f"📝 برای شما مرخصی {UNIT_TITLES[d.unit]} {PAY_TITLES[d.pay_type]} در {when} ثبت شد."
