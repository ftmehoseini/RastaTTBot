"""Excel report builders. Input is service DTOs; output is .xlsx bytes. No bot or DB code here."""

from collections.abc import Callable, Sequence
from datetime import date, datetime
from uuid import UUID

from app.application.dto.attendance import EmployeeReport
from app.application.dto.food import FoodReportRow, Invoice
from app.core.utils.jalali import format_date_numeric, to_jalali, weekday_name
from app.domain.attendance.models import DayStatus, RecordSource
from app.domain.leave.models import PAY_TYPE_FA, STATUS_FA, UNIT_FA, LeaveRequest
from app.domain.users.models import User
from app.infrastructure.excel.styles import (
    MONEY_FORMAT,
    Column,
    add_table_sheet,
    minutes_cell,
    new_workbook,
    to_bytes,
)

DAY_STATUS_FA = {
    DayStatus.COMPLETE: "کامل",
    DayStatus.MISSING_CHECK_OUT: "خروج ثبت نشده",
    DayStatus.MISSING_CHECK_IN: "ورود ثبت نشده",
    DayStatus.ABSENT: "بدون ثبت",
    DayStatus.NON_WORKDAY: "تعطیل",
}

SOURCE_FA = {
    RecordSource.BOT_LIVE: "ربات (لحظه‌ای)",
    RecordSource.MANUAL_CORRECTION: "ثبت فراموشی",
    RecordSource.MANUAL_TODAY: "ثبت دستی امروز",
    RecordSource.USER_EDIT: "ویرایش کارمند",
    RecordSource.ADMIN_EDIT: "ویرایش ادمین",
    RecordSource.LEGACY_IMPORT: "ربات قبلی",
}


def _j(day: date) -> str:
    """Jalali date as plain ASCII text, sortable and filterable in Excel."""
    return to_jalali(day).iso()


def _source(source: RecordSource | None) -> str:
    return SOURCE_FA.get(source, "") if source else ""


def report_filename(prefix: str, start: date, end: date) -> str:
    s, e = to_jalali(start), to_jalali(end)
    return f"{prefix}_{s.year}{s.month:02d}{s.day:02d}-{e.year}{e.month:02d}{e.day:02d}.xlsx"


# --- attendance ---------------------------------------------------------------------------

ATTENDANCE_COLUMNS = [
    Column("نام کارمند", 22),
    Column("شناسه کاربری", 14),
    Column("تاریخ شمسی", 12),
    Column("روز", 10),
    Column("تاریخ میلادی", 12),
    Column("ورود", 8),
    Column("خروج", 8),
    Column("کارکرد", 9),
    Column("موظفی", 9),
    Column("تعجیل (دقیقه)", 11),
    Column("تأخیر مجاز (دقیقه)", 13),
    Column("تأخیر غیرمجاز (دقیقه)", 14),
    Column("اضافه‌کار", 10),
    Column("کسری", 9),
    Column("خالص", 9),
    Column("وضعیت", 14),
    Column("منبع ورود", 14),
    Column("منبع خروج", 14),
    Column("اصلاحیه‌ها", 40),
]


def _over_cap(used: int, cap: int) -> int | str:
    """Minutes over a monthly cap; empty when the report has no cap (not a whole month)."""
    return max(0, used - cap) if cap else ""


def _summary_row(r: EmployeeReport) -> list:
    t = r.totals
    head = [r.full_name, "ساعتی" if r.hourly else "تمام‌وقت", t.present_days, t.incomplete_days]
    if r.hourly:
        return [*head, "", minutes_cell(t.worked_minutes), *[""] * 9]
    return [
        *head,
        t.absent_days,
        minutes_cell(t.worked_minutes),
        minutes_cell(t.required_minutes),
        t.early_departure_minutes,
        _over_cap(t.early_departure_minutes, r.early_cap_minutes),
        t.floating_late_minutes,
        t.late_minutes,
        _over_cap(t.late_minutes, r.late_cap_minutes),
        minutes_cell(t.overtime_minutes),
        minutes_cell(t.deficit_minutes),
        minutes_cell(t.net_balance_minutes),
    ]


def build_attendance_workbook(reports: Sequence[EmployeeReport]) -> bytes:
    workbook = new_workbook()
    rows = []
    for report in reports:
        for row in report.rows:
            c = row.calculation
            check_out = row.check_out_local.strftime("%H:%M") if row.check_out_local else ""
            if row.check_out_local and row.check_in_local and row.check_out_local.date() != row.check_in_local.date():
                check_out += " (+1)"
            rows.append(
                [
                    report.full_name,
                    report.messenger_user_id,
                    _j(row.work_date),
                    weekday_name(row.work_date),
                    row.work_date.isoformat(),
                    row.check_in_local.strftime("%H:%M") if row.check_in_local else "",
                    check_out,
                    minutes_cell(c.gross_worked_minutes),
                    # Hourly staff have no fixed day, so these columns stay empty for them.
                    *(
                        [""] * 7
                        if c.hourly
                        else [
                            minutes_cell(c.required_minutes),
                            c.early_departure_minutes,
                            c.floating_late_minutes,
                            c.late_minutes,
                            minutes_cell(c.overtime_minutes),
                            minutes_cell(c.deficit_minutes),
                            minutes_cell(c.net_balance_minutes),
                        ]
                    ),
                    DAY_STATUS_FA[c.status],
                    _source(row.check_in_source),
                    _source(row.check_out_source),
                    " | ".join(row.correction_notes),
                ]
            )
    add_table_sheet(workbook, "جزئیات حضور", ATTENDANCE_COLUMNS, rows, first=True)

    summary_columns = [
        Column("نام کارمند", 22),
        Column("نوع همکاری", 11),
        Column("روزهای کامل", 11),
        Column("روزهای ناقص", 11),
        Column("غیبت", 8),
        Column("کارکرد", 10),
        Column("موظفی", 10),
        Column("تعجیل (دقیقه)", 11),
        Column("مازاد سقف تعجیل (دقیقه)", 15),
        Column("تأخیر مجاز (دقیقه)", 13),
        Column("تأخیر غیرمجاز (دقیقه)", 14),
        Column("مازاد سقف تأخیر (دقیقه)", 15),
        Column("اضافه‌کار", 10),
        Column("کسری", 10),
        Column("خالص", 10),
    ]
    summary_rows = [_summary_row(r) for r in reports]
    add_table_sheet(workbook, "خلاصه", summary_columns, summary_rows)
    return to_bytes(workbook)


# --- leave ----------------------------------------------------------------------------------


def build_leave_workbook(
    requests: Sequence[LeaveRequest], users: dict[UUID, User], to_local: Callable[[datetime], datetime]
) -> bytes:
    workbook = new_workbook()
    columns = [
        Column("شماره", 8),
        Column("نام کارمند", 22),
        Column("نوع", 10),
        Column("واحد", 9),
        Column("از تاریخ", 12),
        Column("تا تاریخ", 12),
        Column("از ساعت", 8),
        Column("تا ساعت", 8),
        Column("تعداد روز", 9),
        Column("مدت (ساعت)", 10),
        Column("وضعیت", 18),
        Column("دلیل", 30),
        Column("دلیل رد", 30),
        Column("تاریخ ثبت", 12),
    ]
    rows = []
    for r in requests:
        user = users.get(r.user_id)
        rows.append(
            [
                r.display_number,
                user.full_name if user else "",
                PAY_TYPE_FA[r.pay_type],
                UNIT_FA[r.unit],
                _j(r.start_date),
                _j(r.end_date),
                r.start_time.strftime("%H:%M") if r.start_time else "",
                r.end_time.strftime("%H:%M") if r.end_time else "",
                r.days_count or "",
                minutes_cell(r.duration_minutes),
                STATUS_FA[r.status],
                r.reason or "",
                r.rejection_reason or "",
                format_date_numeric(to_local(r.submitted_at).date(), persian_digits=False),
            ]
        )
    add_table_sheet(workbook, "مرخصی‌ها", columns, rows, first=True)
    return to_bytes(workbook)


# --- food -------------------------------------------------------------------------------------


def build_food_orders_workbook(rows: Sequence[FoodReportRow]) -> bytes:
    workbook = new_workbook()
    columns = [
        Column("تاریخ شمسی", 12),
        Column("روز", 10),
        Column("نام کارمند", 22),
        Column("غذا", 24),
        Column("نوع", 9),
        Column("تعداد مهمان", 10),
        Column("قیمت واحد", 12, MONEY_FORMAT),
        Column("مبلغ کل", 12, MONEY_FORMAT),
        Column("وضعیت", 10),
        Column("زمان ثبت", 17),
        Column("توضیحات", 24),
    ]
    data = [
        [
            _j(r.day),
            weekday_name(r.day),
            r.full_name,
            r.food_name,
            "مهمان" if r.is_guest else "کارمند",
            r.guest_count if r.is_guest else "",
            r.unit_price,
            r.total,
            r.status,
            f"{_j(r.created_at.date())} {r.created_at:%H:%M}",
            r.note,
        ]
        for r in rows
    ]
    add_table_sheet(workbook, "سفارش‌ها", columns, data, first=True)
    return to_bytes(workbook)


def build_invoices_workbook(invoices: Sequence[Invoice], currency_label: str) -> bytes:
    workbook = new_workbook()
    columns = [
        Column("نام کارمند", 22),
        Column("تعداد غذای خود", 12),
        Column(f"مبلغ غذای خود ({currency_label})", 16, MONEY_FORMAT),
        Column("تعداد غذای مهمان", 12),
        Column(f"مبلغ مهمان ({currency_label})", 16, MONEY_FORMAT),
        Column(f"جمع قابل پرداخت ({currency_label})", 18, MONEY_FORMAT),
    ]
    rows = [
        [i.full_name, i.employee_meals, i.employee_amount, i.guest_meals, i.guest_amount, i.total] for i in invoices
    ]
    totals = [
        "جمع کل",
        sum(i.employee_meals for i in invoices),
        sum(i.employee_amount for i in invoices),
        sum(i.guest_meals for i in invoices),
        sum(i.guest_amount for i in invoices),
        sum(i.total for i in invoices),
    ]
    add_table_sheet(workbook, "صورت‌حساب هفتگی", columns, rows, totals=totals, first=True)

    line_columns = [
        Column("نام کارمند", 22),
        Column("تاریخ شمسی", 12),
        Column("روز", 10),
        Column("غذا", 24),
        Column("نوع", 9),
        Column("تعداد", 8),
        Column("قیمت واحد", 12, MONEY_FORMAT),
        Column("مبلغ", 12, MONEY_FORMAT),
    ]
    line_rows = [
        [
            i.full_name,
            _j(line.day),
            weekday_name(line.day),
            line.food_name,
            "مهمان" if line.is_guest else "کارمند",
            line.quantity,
            line.unit_price,
            line.total,
        ]
        for i in invoices
        for line in i.lines
    ]
    add_table_sheet(workbook, "ریز اقلام", line_columns, line_rows)
    return to_bytes(workbook)
