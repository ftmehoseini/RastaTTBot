"""Telegram texts for reports read from an admin's Excel workbook. Labels are the workbook's own."""

from datetime import date, datetime

from app.core.utils.jalali import format_date, format_time
from app.core.utils.text import to_persian_digits
from app.infrastructure.excel.workbook import WorkbookReport

PUNCH_LABEL = {"in": "ورود", "out": "خروج"}


def punch_saved_short(kind: str, at_local: datetime) -> str:
    """The reply right after check-in/out: the time only. The calculated report follows from Excel."""
    return (
        f"✅ {PUNCH_LABEL[kind]} شما ثبت شد.\n📅 {format_date(at_local.date())}\n🕘 ساعت {format_time(at_local)}\n"
        "📊 گزارش محاسبه‌شده از فایل اکسل شما چند لحظه‌ی دیگر ارسال می‌شود."
    )


def report_text(full_name: str, work_date: date, report: WorkbookReport) -> str:
    lines = [f"📊 گزارش حضور — {full_name}", f"📅 {format_date(work_date)}", ""]
    lines += [f"▫️ {label}: {to_persian_digits(value)}" for label, value in report.day_values]
    if report.month_values:
        lines += ["", f"📆 جمع ماه {report.sheet}:"]
        lines += [f"▪️ {label}: {to_persian_digits(value)}" for label, value in report.month_values]
    return "\n".join(lines)


def report_failed_for_employee(work_date: date) -> str:
    return (
        f"⚠️ ورود و خروج {format_date(work_date)} ثبت شده است، اما گزارش اکسل آن آماده نشد.\n"
        "مدیر سیستم در جریان قرار گرفت."
    )


def report_failed_for_admin(full_name: str, work_date: date, title: str, reason: str) -> str:
    return (
        f"⚠️ گزارش اکسل «{full_name}» برای {format_date(work_date)} آماده نشد.\n"
        f"📄 فایل: {title}\nعلت: {reason}\n"
        "ساعت‌ها در ربات ثبت شده‌اند؛ پس از اصلاح فایل دوباره نوشته می‌شوند."
    )
