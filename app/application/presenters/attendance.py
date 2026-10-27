"""Persian texts for attendance. Pure functions from DTOs to strings."""

from datetime import date, datetime

from app.application.dto.attendance import DayRow, EmployeeReport, PunchPreview, TeamSummary
from app.core.utils.jalali import format_date, format_date_numeric, format_time
from app.core.utils.text import format_minutes, to_persian_digits
from app.domain.attendance.calculation import DailyCalculation
from app.domain.attendance.models import CorrectionType, DayStatus
from app.domain.attendance.timesheet import SheetMonth, SheetRules

CORRECTION_TYPE_FA = {
    CorrectionType.CHECK_IN: "ورود",
    CorrectionType.CHECK_OUT: "خروج",
    CorrectionType.BOTH: "ورود و خروج",
}


def punch_preview(preview: PunchPreview) -> str:
    action = "ورود" if preview.kind == "in" else "خروج"
    lines = [
        f"⏱ ثبت {action} امروز",
        f"📅 تاریخ: {format_date(preview.at_local.date())}",
        f"🕘 ساعت الان: {format_time(preview.at_local)}",
    ]
    if preview.kind == "out" and preview.work_date != preview.at_local.date():
        lines.append(f"(خروج شیفت روز {format_date(preview.work_date)})")
    lines.append(f"\nزمان {action} چطور ثبت شود؟\n⏱ اتوماتیک: همین ساعت الان\n✍️ دستی: ساعت را خودتان بنویسید")
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
        if calc.hourly:
            return text
        if calc.early_departure_minutes:
            text += f"\n⚠️ تعجیل: {format_minutes(calc.early_departure_minutes)}"
        if calc.overtime_minutes:
            text += f"\n➕ اضافه‌کار: {format_minutes(calc.overtime_minutes)}"
        text += f"\n📊 خالص روز: {format_minutes(calc.net_balance_minutes, signed=True)}"
    return text


def _day_line(row: DayRow) -> str:
    c = row.calculation
    head = f"▫️ {format_date(row.work_date)}"
    if c.status == DayStatus.ABSENT:
        return f"{head}\n   🌴 {row.leave_note}" if row.leave_note else f"{head}\n   ❗️ بدون ثبت ورود و خروج"
    check_in = format_time(c.check_in) if c.check_in else "—"
    check_out = format_time(c.check_out) if c.check_out else "—"
    if c.check_in and c.check_out and c.check_out.date() != c.check_in.date():
        check_out += " (روز بعد)"
    marks = (
        (" ✍️" if row.is_manual_today else "") + (" 📝" if row.is_forgotten else "") + (" ✏️" if row.is_edited else "")
    )
    lines = [f"{head}{marks}", f"   ورود {check_in} | خروج {check_out}"]
    lateness = f"   تأخیر مجاز* {format_minutes(c.floating_late_minutes)} | غیرمجاز* {format_minutes(c.late_minutes)}"
    if c.status == DayStatus.MISSING_CHECK_OUT and row.is_today:
        lines.append("   ⏳ امروز — در حال کار")
        if not c.hourly:
            lines.append(lateness)
    elif c.status == DayStatus.MISSING_CHECK_OUT:
        lines.append("   ⚠️ خروج ثبت نشده")
    elif c.status == DayStatus.MISSING_CHECK_IN:
        lines.append("   ⚠️ ورود ثبت نشده")
    elif c.hourly:
        lines.append(f"   کارکرد {format_minutes(c.gross_worked_minutes)}")
    else:
        lines.append(f"{lateness} | تعجیل* {format_minutes(c.early_departure_minutes)}")
        lines.append(
            f"   اضافه‌کار {format_minutes(c.overtime_minutes)} | کارکرد عادی {format_minutes(c.normal_minutes)}"
        )
    if row.leave_note:
        lines.append(f"   🌴 {row.leave_note}")
    return "\n".join(lines)


def _legend(report: EmployeeReport) -> list[str]:
    marks = []
    if any(r.is_manual_today for r in report.rows):
        marks.append("✍️ = ثبت دستی امروز")
    if any(r.is_forgotten for r in report.rows):
        marks.append("📝 = ثبت فراموشی")
    if any(r.is_edited for r in report.rows):
        marks.append("✏️ = ویرایش‌شده")
    return ["\n" + " | ".join(marks)] if marks else []


def _footnote(rules: SheetRules) -> str:
    return (
        f"\n* تأخیر مجاز: ورود تا {_clock(rules.floating_end)} — قابل جبران با اضافه‌کار"
        f"\n* تأخیر غیرمجاز: ورود بعد از {_clock(rules.floating_end)} — تا {format_minutes(rules.late_cap)} در ماه"
        " قابل جبران، بیشتر از آن غیرقابل جبران"
        f"\n* تعجیل: خروج قبل از {_clock(rules.allowed_exit)} — تا {format_minutes(rules.early_cap)} در ماه قابل جبران"
    )


def _clock(minutes: int) -> str:
    return to_persian_digits(f"{minutes // 60:02d}:{minutes % 60:02d}")


def _split(label: str, used: int, cap: int) -> list[str]:
    """'تأخیر غیرمجاز: ۸:۲۰' plus how much of it is compensable under the monthly cap."""
    over = max(used - cap, 0)
    lines = [f"{label}: {format_minutes(used)} (سقف ماهانه {format_minutes(cap)})"]
    lines.append(f"   قابل جبران {format_minutes(used - over)} | غیرقابل جبران {format_minutes(over)}")
    if over:
        lines.append(f"   ⛔️ {format_minutes(over)} بیش از سقف ماهانه")
    return lines


def _month_figures(m: SheetMonth) -> list[str]:
    """The Excel summary block, in the same terms."""
    lines = [
        f"کارکرد عادی: {format_minutes(m.normal)}",
        f"تأخیر مجاز: {format_minutes(m.floating_late)}",
        *_split("تأخیر غیرمجاز", m.late, m.late_cap),
        *_split("تعجیل", m.early, m.early_cap),
        f"اضافه‌کار: {format_minutes(m.overtime)}",
    ]
    if m.paid_leave:
        lines.append(f"مرخصی با حقوق: {format_minutes(m.paid_leave)}")
    lines += [
        "",
        "— محاسبه‌ی نهایی —",
        f"تأخیر و تعجیل قابل جبران: {format_minutes(m.compensable)}",
        f"اضافه‌کار مازاد بر نیاز: {format_minutes(m.overtime_surplus)}",
        f"تأخیر و تعجیل جبران‌نشده: {format_minutes(m.uncompensated)}",
        f"کارکرد نهایی: {format_minutes(m.final_work)}",
    ]
    return lines


def _days_line(report: EmployeeReport) -> str:
    t = report.totals
    return (
        f"روزهای کامل: {to_persian_digits(t.present_days)} | ناقص: {to_persian_digits(t.incomplete_days)}"
        f" | غیبت: {to_persian_digits(t.absent_days)}"
    )


def employee_report(report: EmployeeReport, title: str = "گزارش هفتگی من", month: EmployeeReport | None = None) -> str:
    """Weekly report: every day plus the week's totals and, when given, the month so far."""
    lines = [
        f"📊 {title}",
        f"{report.full_name}",
        f"از {format_date_numeric(report.start)} تا {format_date_numeric(report.end)}",
        "",
    ]
    if not report.rows:
        lines.append("در این بازه رکوردی ثبت نشده است.")
    lines.extend(_day_line(r) for r in report.rows)
    lines.append("")
    t = report.totals
    if report.hourly or report.sheet is None:
        lines += [
            "— جمع (نیروی ساعتی) —",
            f"روزهای حضور: {to_persian_digits(t.present_days)} | ناقص: {to_persian_digits(t.incomplete_days)}",
            f"کارکرد: {format_minutes(t.worked_minutes)}",
        ]
        lines.extend(_legend(report))
        return "\n".join(lines)
    m = report.sheet
    lines += [
        "— جمع —",
        _days_line(report),
        f"کارکرد عادی: {format_minutes(m.normal)} | اضافه‌کار: {format_minutes(m.overtime)}",
        f"تأخیر مجاز: {format_minutes(m.floating_late)} | تأخیر غیرمجاز: {format_minutes(m.late)}",
        f"تعجیل: {format_minutes(m.early)}",
    ]
    if month is not None and month.sheet is not None:
        mm = month.sheet
        lines += [
            "",
            "— ماه جاری تا امروز —",
            *_split("تأخیر غیرمجاز", mm.late, mm.late_cap),
            *_split("تعجیل", mm.early, mm.early_cap),
            f"تأخیر و تعجیل جبران‌نشده: {format_minutes(mm.uncompensated)}",
        ]
    lines.extend(_legend(report))
    if report.sheet_rules is not None:
        lines.append(_footnote(report.sheet_rules))
    return "\n".join(lines)


def monthly_report(report: EmployeeReport, title: str = "گزارش ماهانه من") -> str:
    """Month totals as in the Excel summary block; the days are in the Excel file."""
    lines = [
        f"📅 {title}",
        f"{report.full_name}",
        f"از {format_date_numeric(report.start)} تا {format_date_numeric(report.end)}",
        "",
    ]
    t = report.totals
    if report.hourly or report.sheet is None:
        lines += [
            "— جمع (نیروی ساعتی) —",
            f"روزهای حضور: {to_persian_digits(t.present_days)} | ناقص: {to_persian_digits(t.incomplete_days)}",
            f"کارکرد: {format_minutes(t.worked_minutes)}",
        ]
        return "\n".join(lines)
    lines += ["— جمع ماه —", _days_line(report), *_month_figures(report.sheet)]
    lines.extend(_legend(report))
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


def correction_review(
    kind: CorrectionType, day: date, check_in: str | None, check_out: str | None, title: str = "مرور ثبت فراموشی"
) -> str:
    lines = [f"📝 {title}", f"نوع: {CORRECTION_TYPE_FA[kind]}", f"تاریخ: {format_date(day)}"]
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
