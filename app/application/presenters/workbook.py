"""Telegram texts for reports read from an admin's Excel workbook. Labels are the workbook's own."""

from datetime import date, datetime

from app.core.utils.jalali import format_date, format_date_numeric, format_time, to_jalali
from app.core.utils.text import to_persian_digits
from app.infrastructure.excel.workbook import PeriodReport, WorkbookReport, normalize_label

PUNCH_LABEL = {"in": "ورود", "out": "خروج"}


REPORT_FOLLOWS = "📊 گزارش محاسبه‌شده از فایل اکسل شما چند لحظه‌ی دیگر ارسال می‌شود."


def punch_saved_short(kind: str, at_local: datetime) -> str:
    """The reply right after check-in/out: the time only. After a check-out the Excel report follows;
    after a check-in there is none (the day isn't complete yet)."""
    text = f"✅ {PUNCH_LABEL[kind]} شما ثبت شد.\n📅 {format_date(at_local.date())}\n🕘 ساعت {format_time(at_local)}"
    return f"{text}\n{REPORT_FOLLOWS}" if kind == "out" else text


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


def pick_totals(values: list[tuple[str, str]], wanted: list[str]) -> list[tuple[str, str]]:
    """The configured month totals in the configured order; every total if none of them is in the file."""
    if not wanted:
        return values
    by_label = {normalize_label(label): (label, value) for label, value in values}
    picked = [by_label[normalize_label(w)] for w in wanted if normalize_label(w) in by_label]
    return picked or values


def _fa(value: str) -> str:
    return to_persian_digits(value)


def period_text(
    title: str,
    full_name: str,
    start: date,
    end: date,
    report: PeriodReport,
    dates: list[date],
    wanted: list[str],
) -> str:
    """Weekly (with days) or monthly (totals only) report; every figure is a cell of the Excel file."""
    lines = [f"{title} (از فایل اکسل)", full_name, f"از {format_date_numeric(start)} تا {format_date_numeric(end)}"]
    rows = {(d.month, d.day): d for d in report.days}
    if dates:
        lines.append("")
    for day in dates:
        j = to_jalali(day)
        row = rows.get((j.month, j.day))
        head = f"▫️ {format_date(day)}"
        if row is None:
            lines.append(f"{head}: برگه‌ی این ماه در فایل نیست")
            continue
        if not row.check_in and not row.check_out:
            head += ": بدون ثبت ورود و خروج"
        else:
            head += f": ورود {_fa(row.check_in or '—')} | خروج {_fa(row.check_out or '—')}"
        lines.append(head)
        shown = [f"{label} {_fa(value)}" for label, value in row.values if value not in ("0:00", "0")]
        if shown:
            lines.append("   " + " | ".join(shown))
        if row.notes:
            lines.append(f"   📝 {row.notes}")
    totals = pick_totals(report.month_values, wanted)
    lines += ["", f"📆 جمع ماه {report.sheet} تا امروز:"]
    lines += [f"▪️ {label}: {_fa(value)}" for label, value in totals] or ["—"]
    return "\n".join(lines)


def period_failed() -> str:
    return "⚠️ گزارش از فایل اکسل شما آماده نشد. مدیر سیستم در جریان قرار گرفت؛ کمی بعد دوباره امتحان کنید."
