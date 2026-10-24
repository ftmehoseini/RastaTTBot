"""Persian texts for attendance. Pure functions from DTOs to strings."""

from datetime import date, datetime

from app.application.dto.attendance import DayRow, EmployeeReport, PunchPreview, TeamSummary
from app.core.utils.jalali import format_date, format_date_numeric, format_time
from app.core.utils.text import format_minutes, to_persian_digits
from app.domain.attendance.calculation import DailyCalculation
from app.domain.attendance.models import CorrectionType, DayStatus

CORRECTION_TYPE_FA = {
    CorrectionType.CHECK_IN: "ورود",
    CorrectionType.CHECK_OUT: "خروج",
    CorrectionType.BOTH: "ورود و خروج",
}


def punch_preview(preview: PunchPreview) -> str:
    action = "ورود" if preview.kind == "in" else "خروج"
    lines = [
        f"⏱ ثبت {action}",
        f"📅 تاریخ: {format_date(preview.at_local.date())}",
        f"🕘 ساعت: {format_time(preview.at_local)}",
    ]
    if preview.kind == "out" and preview.work_date != preview.at_local.date():
        lines.append(f"(خروج شیفت روز {format_date(preview.work_date)})")
    lines.append(f"\nآیا {action} با همین زمان ثبت شود؟")
    return "\n".join(lines)


def already_registered(kind: str, at_local: datetime) -> str:
    action = "ورود" if kind == "in" else "خروج"
    return f"ℹ️ {action} شما برای امروز قبلاً ساعت {format_time(at_local)} ثبت شده است."


def punch_saved(kind: str, at_local: datetime, calc: DailyCalculation | None) -> str:
    if kind == "in":
        text = f"✅ ورود شما ثبت شد.\n📅 {format_date(at_local.date())}\n🕘 ساعت {format_time(at_local)}"
        if calc and calc.floating_late_minutes:
            text += f"\n🟢 تأخیر مجاز: {format_minutes(calc.floating_late_minutes)}"
        if calc and calc.late_minutes:
            text += f"\n⚠️ تأخیر غیرمجاز: {format_minutes(calc.late_minutes)}"
        return text
    text = f"🏁 خروج شما ثبت شد.\n📅 {format_date(at_local.date())}\n🕘 ساعت {format_time(at_local)}"
    if calc and calc.gross_worked_minutes is not None:
        text += f"\n⏳ کارکرد امروز: {format_minutes(calc.gross_worked_minutes)}"
        if calc.early_departure_minutes:
            text += f"\n⚠️ تعجیل: {format_minutes(calc.early_departure_minutes)}"
        if calc.overtime_minutes:
            text += f"\n➕ اضافه‌کار: {format_minutes(calc.overtime_minutes)}"
        text += f"\n📊 خالص روز: {format_minutes(calc.net_balance_minutes, signed=True)}"
    return text


def today_status(day: date, calc: DailyCalculation) -> str:
    lines = [f"📋 وضعیت امروز — {format_date(day)}"]
    lines.append(f"ورود: {format_time(calc.check_in) if calc.check_in else 'ثبت نشده'}")
    lines.append(f"خروج: {format_time(calc.check_out) if calc.check_out else 'ثبت نشده'}")
    if calc.check_out:
        lines.append(f"تعجیل: {format_minutes(calc.early_departure_minutes)}")
    if calc.check_in:
        lines.append(f"تأخیر مجاز: {format_minutes(calc.floating_late_minutes)}")
        lines.append(f"تأخیر غیرمجاز: {format_minutes(calc.late_minutes)}")
    if calc.gross_worked_minutes is not None:
        lines.append(f"اضافه‌کار: {format_minutes(calc.overtime_minutes)}")
        lines.append(f"کارکرد: {format_minutes(calc.gross_worked_minutes)}")
        lines.append(f"کسری: {format_minutes(calc.deficit_minutes)}")
        lines.append(f"خالص: {format_minutes(calc.net_balance_minutes, signed=True)}")
    elif calc.check_in and calc.check_in.tzinfo:
        lines.append("⏳ در حال کار")
    return "\n".join(lines)


def _day_line(row: DayRow) -> str:
    c = row.calculation
    head = f"▫️ {format_date(row.work_date)}"
    if c.status == DayStatus.ABSENT:
        return f"{head}\n   ❗️ بدون ثبت ورود و خروج"
    check_in = format_time(c.check_in) if c.check_in else "—"
    check_out = format_time(c.check_out) if c.check_out else "—"
    if c.check_in and c.check_out and c.check_out.date() != c.check_in.date():
        check_out += " (روز بعد)"
    manual = " ✍️" if row.is_manual else ""
    lines = [f"{head}{manual}", f"   ورود {check_in} | خروج {check_out}"]
    if c.status == DayStatus.MISSING_CHECK_OUT:
        lines.append("   ⚠️ خروج ثبت نشده")
    elif c.status == DayStatus.MISSING_CHECK_IN:
        lines.append("   ⚠️ ورود ثبت نشده")
    else:
        lines.append(
            f"   تعجیل {format_minutes(c.early_departure_minutes)} | تأخیر مجاز {format_minutes(c.floating_late_minutes)}"
            f" | غیرمجاز {format_minutes(c.late_minutes)}"
        )
        lines.append(
            f"   اضافه‌کار {format_minutes(c.overtime_minutes)} | کارکرد {format_minutes(c.gross_worked_minutes)}"
            f" | کسری {format_minutes(c.deficit_minutes)}"
        )
    return "\n".join(lines)


def _with_cap(label: str, used: int, cap: int) -> str:
    """'تعجیل: ۱:۲۰ از سقف ۳:۰۰ (مانده ۱:۴۰)', or the overrun when the cap is passed."""
    line = f"{label}: {format_minutes(used)}"
    if not cap:
        return line
    line += f" از سقف {format_minutes(cap)}"
    if used > cap:
        return f"{line}\n   ⛔️ {format_minutes(used - cap)} بیش از سقف ماهانه"
    return f"{line} (مانده {format_minutes(cap - used)})"


def employee_report(report: EmployeeReport, title: str = "گزارش هفتگی من", with_days: bool = True) -> str:
    lines = [
        f"📊 {title}",
        f"{report.full_name}",
        f"از {format_date_numeric(report.start)} تا {format_date_numeric(report.end)}",
        "",
    ]
    if not report.rows:
        lines.append("در این بازه رکوردی ثبت نشده است.")
    if with_days:
        lines.extend(_day_line(r) for r in report.rows)
        lines.append("")
    t = report.totals
    lines += [
        "— جمع —",
        f"روزهای کامل: {to_persian_digits(t.present_days)} | ناقص: {to_persian_digits(t.incomplete_days)}"
        f" | غیبت: {to_persian_digits(t.absent_days)}",
        f"کارکرد: {format_minutes(t.worked_minutes)} از موظفی {format_minutes(t.required_minutes)}",
        _with_cap("تعجیل", t.early_departure_minutes, report.early_cap_minutes),
        f"تأخیر مجاز: {format_minutes(t.floating_late_minutes)}",
        _with_cap("تأخیر غیرمجاز", t.late_minutes, report.late_cap_minutes),
        f"اضافه‌کار: {format_minutes(t.overtime_minutes)} | کسری: {format_minutes(t.deficit_minutes)}",
        f"خالص: {format_minutes(t.net_balance_minutes, signed=True)}",
    ]
    if any(r.is_manual for r in report.rows):
        lines.append("\n✍️ = ثبت دستی (فراموشی)")
    return "\n".join(lines)


def team_summary(summary: TeamSummary, title: str) -> str:
    same_day = summary.start == summary.end
    period = (
        format_date(summary.start)
        if same_day
        else f"{format_date_numeric(summary.start)} تا {format_date_numeric(summary.end)}"
    )
    unit = "نفر" if same_day else "روز-نفر"
    return "\n".join(
        [
            f"📊 {title}",
            period,
            f"تعداد کارکنان: {to_persian_digits(summary.employee_count)}",
            f"حضور کامل: {to_persian_digits(summary.present_count)} {unit}",
            f"بدون ثبت: {to_persian_digits(summary.absent_count)} {unit}",
            f"خروج ثبت‌نشده: {to_persian_digits(summary.missing_checkout_count)} {unit}",
            f"دارای تأخیر: {to_persian_digits(summary.late_count)} {unit}",
            f"ثبت دستی: {to_persian_digits(summary.manual_count)} {unit}",
            "",
            "فایل اکسل جزئیات در پیام بعدی ارسال می‌شود.",
        ]
    )


def correction_review(kind: CorrectionType, day: date, check_in: str | None, check_out: str | None) -> str:
    lines = ["📝 مرور ثبت فراموشی", f"نوع: {CORRECTION_TYPE_FA[kind]}", f"تاریخ: {format_date(day)}"]
    if check_in:
        lines.append(f"ساعت ورود: {to_persian_digits(check_in)}")
    if check_out:
        lines.append(f"ساعت خروج: {to_persian_digits(check_out)}")
    lines.append("\nآیا ثبت شود؟")
    return "\n".join(lines)


def check_in_reminder(day: date) -> str:
    return (
        f"⏰ یادآوری: ورود امروز ({format_date(day)}) هنوز ثبت نشده است.\n"
        "اگر سر کار هستید، از منوی «⏱ ورود و خروج» ورود را ثبت کنید."
    )


def check_out_reminder(day: date, check_in: datetime) -> str:
    return (
        f"⏰ یادآوری: ورود شما امروز ({format_date(day)}) ساعت {format_time(check_in)} ثبت شده ولی خروج ثبت نشده است.\n"
        "لطفاً هنگام رفتن خروج را ثبت کنید."
    )
