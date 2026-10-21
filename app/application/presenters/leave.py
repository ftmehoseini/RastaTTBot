"""Persian texts for leave requests."""

from app.application.dto.leave import LeaveQuote
from app.core.utils.jalali import format_date
from app.core.utils.text import format_minutes, to_persian_digits
from app.domain.leave.models import PAY_TYPE_FA, STATUS_FA, UNIT_FA, LeaveRequest, LeaveStatus, LeaveUnit
from app.domain.users.models import User


def _period(unit: LeaveUnit, start, end, start_time, end_time) -> list[str]:
    if unit == LeaveUnit.HOURLY:
        return [
            f"تاریخ: {format_date(start)}",
            f"ساعت: {to_persian_digits(start_time.strftime('%H:%M'))} تا {to_persian_digits(end_time.strftime('%H:%M'))}",
        ]
    if start == end:
        return [f"تاریخ: {format_date(start)}"]
    return [f"از: {format_date(start)}", f"تا: {format_date(end)}"]


def _duration(days: int | None, minutes: int | None) -> str:
    if days is not None:
        return f"{to_persian_digits(days)} روز"
    return f"{format_minutes(minutes)} ساعت"


def quote_review(quote: LeaveQuote) -> str:
    d = quote.draft
    lines = [
        "📝 مرور درخواست مرخصی",
        f"نوع: {PAY_TYPE_FA[d.pay_type]} — {UNIT_FA[d.unit]}",
        *_period(d.unit, d.start_date, d.end_date, d.start_time, d.end_time),
        f"مدت: {_duration(quote.days_count, quote.duration_minutes)}",
        f"توضیحات: {d.reason or '—'}",
        "",
        "آیا درخواست ارسال شود؟",
    ]
    return "\n".join(lines)


def request_details(request: LeaveRequest, employee: User | None = None, step_title: str | None = None) -> str:
    lines = [f"📝 درخواست مرخصی شماره {to_persian_digits(request.display_number)}"]
    if employee is not None:
        lines.append(f"کارمند: {employee.full_name}")
    lines += [
        f"نوع: {PAY_TYPE_FA[request.pay_type]} — {UNIT_FA[request.unit]}",
        *_period(request.unit, request.start_date, request.end_date, request.start_time, request.end_time),
        f"مدت: {_duration(request.days_count, request.duration_minutes)}",
        f"توضیحات: {request.reason or '—'}",
        f"وضعیت: {STATUS_FA[request.status]}",
    ]
    if step_title:
        lines.append(f"مرحله‌ی فعلی: {step_title}")
    if request.status == LeaveStatus.REJECTED and request.rejection_reason:
        lines.append(f"دلیل رد: {request.rejection_reason}")
    return "\n".join(lines)


def request_line(request: LeaveRequest) -> str:
    period = format_date(request.start_date)
    if request.unit == LeaveUnit.DAILY and request.end_date != request.start_date:
        period += f" تا {format_date(request.end_date)}"
    return (
        f"#{to_persian_digits(request.display_number)} | {PAY_TYPE_FA[request.pay_type]} {UNIT_FA[request.unit]}"
        f" | {period}\n   {STATUS_FA[request.status]}"
    )


def submitted_to_employee(request: LeaveRequest) -> str:
    return (
        f"✅ درخواست مرخصی شماره {to_persian_digits(request.display_number)} ثبت شد "
        "و برای بررسی منشی ارسال شد.\nنتیجه از طریق همین ربات به شما اطلاع داده می‌شود."
    )


def advanced_to_employee(request: LeaveRequest) -> str:
    return (
        f"🔄 درخواست مرخصی شماره {to_persian_digits(request.display_number)} توسط منشی تأیید شد "
        "و برای تأیید نهایی به مدیر ارسال شد."
    )


def approved_to_employee(request: LeaveRequest) -> str:
    return f"✅ درخواست مرخصی شماره {to_persian_digits(request.display_number)} تأیید نهایی شد.\n\n" + request_details(
        request
    )


def rejected_to_employee(request: LeaveRequest) -> str:
    return (
        f"❌ درخواست مرخصی شماره {to_persian_digits(request.display_number)} رد شد.\n"
        f"دلیل: {request.rejection_reason or '—'}"
    )
