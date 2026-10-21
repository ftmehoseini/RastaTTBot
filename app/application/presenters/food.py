"""Persian texts for food menus, orders, invoices and admin summaries."""

from app.application.dto.food import DayMenu, DaySummary, Invoice, MenuImportPreview
from app.core.config.policy import FoodPolicy, PaymentPolicy
from app.core.utils.jalali import format_date, format_date_numeric, format_date_short, format_time
from app.core.utils.text import format_money, to_persian_digits
from app.domain.food.models import FoodGuestOrder, FoodOrder
from app.infrastructure.excel.menu_parser import MenuParseResult


def week_menu(days: list[DayMenu], policy: FoodPolicy) -> str:
    if not days:
        return "🍽 هنوز منوی این هفته بارگذاری نشده است."
    lines = ["🍽 منوی این هفته", ""]
    for day in days:
        status = "" if day.is_open else " (مهلت سفارش تمام شده)"
        lines.append(f"📅 {format_date_short(day.day)}{status}")
        for item in day.items:
            lines.append(f"   • {item.food_name} — {format_money(item.price_toman, policy.currency_label)}")
        lines.append("")
    lines.append(
        f"⏰ مهلت سفارش هر روز: تا ساعت {to_persian_digits(policy.ordering_deadline_time.strftime('%H:%M'))} همان روز"
    )
    return "\n".join(lines)


def day_menu(day: DayMenu, policy: FoodPolicy, current: FoodOrder | None) -> str:
    lines = [f"🍽 {format_date(day.day)}", ""]
    for item in day.items:
        lines.append(f"• {item.food_name} — {format_money(item.price_toman, policy.currency_label)}")
    lines.append("")
    if current is not None and current.items:
        names = "، ".join(i.food_name for i in current.items)
        lines.append(f"✅ سفارش فعلی شما: {names}")
    lines.append(f"⏰ مهلت: ساعت {format_time(day.deadline)}")
    if day.is_open:
        lines.append("\nغذای موردنظر را انتخاب کنید:")
    else:
        lines.append("\nمهلت سفارش این روز تمام شده است.")
    return "\n".join(lines)


def order_confirm(day_text: str, food_name: str, price: int, policy: FoodPolicy) -> str:
    return (
        f"🧾 تأیید سفارش\n📅 {day_text}\n🍛 {food_name}\n💰 {format_money(price, policy.currency_label)}\n\n"
        "آیا ثبت شود؟"
    )


def order_saved(order: FoodOrder, policy: FoodPolicy) -> str:
    names = "، ".join(f"{i.food_name}" for i in order.items)
    return (
        f"✅ سفارش {format_date(order.order_date)} ثبت شد: {names}\n"
        f"مبلغ: {format_money(order.total_price, policy.currency_label)}"
    )


def guest_review(day_text: str, food_name: str, price: int, count: int, policy: FoodPolicy) -> str:
    return (
        "👥 تأیید سفارش مهمان\n"
        f"📅 {day_text}\n🍛 {food_name}\n"
        f"تعداد: {to_persian_digits(count)} پرس × {format_money(price, policy.currency_label)}\n"
        f"💰 جمع: {format_money(price * count, policy.currency_label)}\n\nآیا ثبت شود؟"
    )


def guest_saved(order: FoodGuestOrder, policy: FoodPolicy) -> str:
    return (
        f"✅ سفارش مهمان ثبت شد: {to_persian_digits(order.guest_count)} پرس {order.food_name} "
        f"برای {format_date(order.order_date)}\n"
        f"مبلغ: {format_money(order.total_price, policy.currency_label)}"
    )


def invoice(inv: Invoice, policy: FoodPolicy, payment: PaymentPolicy, title: str = "صورت‌حساب غذای این هفته") -> str:
    lines = [
        f"🧾 {title}",
        f"{inv.full_name}",
        f"از {format_date_numeric(inv.week_start)} تا {format_date_numeric(inv.week_end)}",
        "",
    ]
    if not inv.lines:
        lines.append("این هفته سفارشی ثبت نکرده‌اید.")
    for line in inv.lines:
        kind = f"👥 مهمان × {to_persian_digits(line.quantity)}" if line.is_guest else "🍛"
        lines.append(
            f"{format_date_short(line.day)} | {kind} {line.food_name} | {format_money(line.total, policy.currency_label)}"
        )
    lines += [
        "",
        f"غذای خود: {to_persian_digits(inv.employee_meals)} پرس — {format_money(inv.employee_amount, policy.currency_label)}",
        f"مهمان: {to_persian_digits(inv.guest_meals)} پرس — {format_money(inv.guest_amount, policy.currency_label)}",
        f"💰 جمع قابل پرداخت: {format_money(inv.total, policy.currency_label)}",
    ]
    if inv.total and (payment.shaba_number or payment.payment_message):
        lines.append("")
        lines.append("💳 اطلاعات پرداخت")
        if payment.shaba_number:
            lines.append(f"شماره شبا: {payment.shaba_number}")
        if payment.account_holder_name:
            lines.append(f"به نام: {payment.account_holder_name}")
        if payment.payment_message:
            lines.append(payment.payment_message)
    return "\n".join(lines)


def day_summaries(summaries: list[DaySummary], title: str) -> str:
    if not summaries:
        return f"📊 {title}\nسفارشی ثبت نشده است."
    lines = [f"📊 {title}", ""]
    for s in summaries:
        lines.append(f"📅 {format_date_short(s.day)}")
        for name, count in sorted(s.employee_counts.items(), key=lambda kv: -kv[1]):
            lines.append(f"{name}: {to_persian_digits(count)} پرس")
        if s.guest_total:
            guests = "، ".join(f"{n} {to_persian_digits(c)}" for n, c in s.guest_counts.items())
            lines.append(f"مهمان: {to_persian_digits(s.guest_total)} پرس ({guests})")
        lines.append(f"جمع کل: {to_persian_digits(s.employee_total + s.guest_total)} پرس")
        lines.append("")
    return "\n".join(lines).strip()


def import_errors(result: MenuParseResult) -> str:
    return "❌ فایل منو پذیرفته نشد:\n\n" + "\n".join(f"• {e}" for e in result.errors)


def import_preview(preview: MenuImportPreview, result: MenuParseResult, policy: FoodPolicy) -> str:
    lines = [
        "📋 پیش‌نمایش منو",
        f"هفته: {format_date_numeric(preview.week_start)} تا {format_date_numeric(preview.week_end)}",
        f"تعداد روز: {to_persian_digits(preview.day_count)} | تعداد غذا: {to_persian_digits(preview.item_count)}",
        "",
    ]
    current_day = None
    for row in sorted(result.rows, key=lambda r: (r.menu_date, r.row_number)):
        if row.menu_date != current_day:
            current_day = row.menu_date
            lines.append(f"📅 {format_date_short(row.menu_date)}")
        inactive = " (غیرفعال)" if not row.is_active else ""
        lines.append(f"   • {row.food_name} — {format_money(row.price_toman, policy.currency_label)}{inactive}")
    lines.append("")
    if preview.current_version:
        lines.append(
            f"⚠️ برای این هفته منوی نسخه‌ی {to_persian_digits(preview.current_version)} وجود دارد و جایگزین می‌شود."
        )
        if preview.affected_orders:
            lines.append(
                f"⚠️ {to_persian_digits(preview.affected_orders)} سفارش ثبت‌شده مربوط به غذاهایی است که در منوی جدید نیستند."
                " این سفارش‌ها با همان غذا و قیمت قبلی معتبر می‌مانند."
            )
        if preview.price_changes:
            lines.append(
                f"ℹ️ قیمت {to_persian_digits(preview.price_changes)} غذا تغییر کرده؛ سفارش‌های قبلی با قیمت قبلی محاسبه می‌شوند."
            )
    lines.append("آیا منو ثبت شود؟")
    return "\n".join(lines)


def broadcast_message(menu_week_text: str) -> str:
    return f"🍽 منوی غذای هفته ({menu_week_text}) به‌روزرسانی شد.\nلطفاً سفارش غذای خود را قبل از پایان مهلت ثبت کنید."
