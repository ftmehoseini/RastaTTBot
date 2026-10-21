"""Food administration: menu upload with preview, broadcast, reports and invoices."""

import asyncio
import io
import uuid
from datetime import timedelta
from pathlib import Path
from uuid import UUID

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.application.presenters import food as texts
from app.application.services import access
from app.application.services.container import Services
from app.application.use_cases.food_broadcast import broadcast_menu
from app.core.config.settings import get_settings
from app.core.exceptions import ExpiredActionError, PermissionDeniedError, ValidationError
from app.core.logging import get_logger
from app.core.security.rbac import Permission
from app.core.utils.jalali import format_date_numeric, week_range
from app.core.utils.text import to_persian_digits
from app.domain.users.models import User
from app.infrastructure.excel.menu_parser import build_menu_template, parse_menu
from app.infrastructure.excel.reports import build_food_orders_workbook, build_invoices_workbook, report_filename
from app.presentation.bot.callbacks.data import FoodAdminCb
from app.presentation.bot.handlers.common import query_message, send_file, show
from app.presentation.bot.keyboards.common import back_row, button, cancel_row, confirm_rows, inline
from app.presentation.bot.middlewares.filters import IsRegistered
from app.presentation.bot.states.flows import MenuUpload

log = get_logger(__name__)

router = Router(name="food_admin")
router.message.filter(IsRegistered())
router.callback_query.filter(IsRegistered())

TITLE = "⚙️ مدیریت غذا"


def admin_markup(user: User):
    rows = []
    if access.can(user, Permission.FOOD_MENU_MANAGE):
        rows.append(
            [button("📤 آپلود/به‌روزرسانی منوی هفته", FoodAdminCb(a="upload").pack())],
        )
        rows.append([button("📄 دریافت قالب اکسل منو", FoodAdminCb(a="template").pack())])
    if access.can(user, Permission.FOOD_BROADCAST):
        rows.append([button("📣 اطلاع‌رسانی ثبت غذا", FoodAdminCb(a="notify").pack())])
    if access.can(user, Permission.FOOD_REPORT_ALL):
        rows.append(
            [
                button("📊 گزارش این هفته", FoodAdminCb(a="summary", v="0").pack()),
                button("📊 گزارش هفته بعد", FoodAdminCb(a="summary", v="1").pack()),
            ]
        )
        rows.append(
            [
                button("📥 فایل سفارش‌ها", FoodAdminCb(a="orders_xls").pack()),
                button("🧾 صورت‌حساب‌ها", FoodAdminCb(a="invoices").pack()),
            ]
        )
    rows.append(back_row("food"))
    return inline(rows)


def _store_upload(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _require_any_food_admin(user: User) -> None:
    if not (access.can(user, Permission.FOOD_MENU_MANAGE) or access.can(user, Permission.FOOD_REPORT_ALL)):
        raise PermissionDeniedError()


@router.callback_query(FoodAdminCb.filter(F.a == "menu"))
async def admin_menu(query: CallbackQuery, state: FSMContext, user: User) -> None:
    _require_any_food_admin(user)
    await state.clear()
    await show(query, TITLE, admin_markup(user))


# --- template and upload --------------------------------------------------------------------------


@router.callback_query(FoodAdminCb.filter(F.a == "template"))
async def template(query: CallbackQuery, services: Services, user: User) -> None:
    access.require(user, Permission.FOOD_MENU_MANAGE)
    week_start = week_range(services.time.today() + timedelta(days=7))[0]
    await query.answer()
    content = build_menu_template(week_start)
    await send_file(
        query_message(query),
        content,
        report_filename("menu_template", week_start, week_start + timedelta(days=6)),
        caption="قالب منوی هفته بعد. ستون‌ها را پر کنید و همین فایل را ارسال کنید.",
    )


@router.callback_query(FoodAdminCb.filter(F.a == "upload"))
async def upload_start(query: CallbackQuery, state: FSMContext, user: User) -> None:
    access.require(user, Permission.FOOD_MENU_MANAGE)
    await state.set_state(MenuUpload.waiting_file)
    await show(
        query,
        "📤 فایل اکسل منوی هفته (‎.xlsx‎) را ارسال کنید.\nاگر قالب ندارید، «دریافت قالب اکسل منو» را بزنید.",
        inline([cancel_row()]),
    )


@router.message(StateFilter(MenuUpload.waiting_file), F.document)
async def upload_file(message: Message, state: FSMContext, services: Services, user: User, bot: Bot) -> None:
    access.require(user, Permission.FOOD_MENU_MANAGE)
    settings = get_settings()
    document = message.document
    assert document is not None
    filename = document.file_name or "menu.xlsx"
    if not filename.lower().endswith(".xlsx"):
        raise ValidationError("فقط فایل اکسل با پسوند ‎.xlsx‎ پذیرفته می‌شود.")
    if document.file_size and document.file_size > settings.max_upload_bytes:
        raise ValidationError(
            f"حجم فایل بیشتر از حد مجاز ({to_persian_digits(settings.max_upload_bytes // 1024)} کیلوبایت) است."
        )
    buffer = io.BytesIO()
    await bot.download(document, destination=buffer)
    content = buffer.getvalue()
    if len(content) > settings.max_upload_bytes:
        raise ValidationError("حجم فایل بیشتر از حد مجاز است.")

    parsed = parse_menu(content, services.time.today(), services.policy.food.max_price_toman)
    if not parsed.ok:
        await message.answer(
            texts.import_errors(parsed) + "\n\nفایل اصلاح‌شده را دوباره ارسال کنید یا /cancel را بزنید."
        )
        return
    preview = await services.food.preview_import(user, parsed)
    stored = settings.upload_dir / f"menu_{uuid.uuid4().hex}.xlsx"
    await asyncio.to_thread(_store_upload, stored, content)
    await state.set_state(MenuUpload.confirming)
    await state.update_data(menu_path=str(stored), menu_filename=filename[:200])
    await message.answer(
        texts.import_preview(preview, parsed, services.policy.food),
        reply_markup=inline(confirm_rows(FoodAdminCb(a="confirm").pack())),
    )


@router.message(StateFilter(MenuUpload.waiting_file))
async def upload_not_a_file(message: Message) -> None:
    await message.answer("لطفاً فایل اکسل منو را به‌صورت فایل (Document) ارسال کنید یا /cancel را بزنید.")


@router.callback_query(StateFilter(MenuUpload.confirming), FoodAdminCb.filter(F.a == "confirm"))
async def upload_confirm(query: CallbackQuery, state: FSMContext, services: Services, user: User) -> None:
    data = await state.get_data()
    await state.clear()
    path = Path(data.get("menu_path", ""))
    if not await asyncio.to_thread(path.is_file):
        raise ExpiredActionError("فایل موقت پیدا نشد. لطفاً دوباره آپلود کنید.")
    try:
        content = await asyncio.to_thread(path.read_bytes)
        parsed = parse_menu(content, services.time.today(), services.policy.food.max_price_toman)
        if not parsed.ok:
            raise ValidationError(texts.import_errors(parsed))
        kept: Path | None = None
        if services.policy.food.retain_menu_files:
            kept = path.parent / "menus" / path.name
            await asyncio.to_thread(kept.parent.mkdir, parents=True, exist_ok=True)
        result = await services.food.import_menu(user, parsed, data.get("menu_filename"), str(kept) if kept else None)
        if kept is not None:
            await asyncio.to_thread(path.replace, kept)
    finally:
        await asyncio.to_thread(path.unlink, missing_ok=True)
    menu = result.menu
    lines = [
        f"✅ منوی هفته‌ی {format_date_numeric(menu.week_start_date)} (نسخه {to_persian_digits(menu.version)}) ثبت شد.",
    ]
    if result.remapped_orders or result.orphaned_orders:
        lines.append(
            f"سفارش‌های منتقل‌شده به منوی جدید: {to_persian_digits(result.remapped_orders)} | "
            f"سفارش‌های غذای حذف‌شده (معتبر با قیمت قبلی): {to_persian_digits(result.orphaned_orders)}"
        )
    markup = None
    if access.can(user, Permission.FOOD_BROADCAST):
        lines.append("\nبرای اطلاع کارکنان، «اطلاع‌رسانی ثبت غذا» را بزنید.")
        markup = inline([[button("📣 اطلاع‌رسانی ثبت غذا", FoodAdminCb(a="notify").pack())], back_row("food")])
    await show(query, "\n".join(lines), markup)


@router.callback_query(FoodAdminCb.filter(F.a == "confirm"))
async def upload_confirm_stale(query: CallbackQuery) -> None:
    raise ExpiredActionError()


# --- broadcast -----------------------------------------------------------------------------------------


@router.callback_query(FoodAdminCb.filter(F.a == "notify"))
async def notify_preview(query: CallbackQuery, services: Services, user: User) -> None:
    recipients = await services.food.broadcast_recipients(user)
    menu = await services.food.latest_menu()
    if menu is None:
        await show(query, "هنوز منویی ثبت نشده است.", inline([back_row("food")]))
        return
    already = (
        "\n⚠️ برای این نسخه قبلاً اطلاع‌رسانی شده؛ فقط به افرادی که پیام نگرفته‌اند ارسال می‌شود."
        if menu.notified_at
        else ""
    )
    text = (
        f"📣 اطلاع‌رسانی منوی هفته‌ی {format_date_numeric(menu.week_start_date)}\n"
        f"تعداد گیرندگان: {to_persian_digits(len(recipients))} نفر{already}\n\nارسال شود؟"
    )
    await show(query, text, inline(confirm_rows(FoodAdminCb(a="notify_ok", v=menu.id.hex).pack())))


@router.callback_query(FoodAdminCb.filter(F.a == "notify_ok"))
async def notify_send(query: CallbackQuery, callback_data: FoodAdminCb, services: Services, user: User) -> None:
    access.require(user, Permission.FOOD_BROADCAST)
    menu_id = UUID(hex=callback_data.v)
    try:
        from app.infrastructure.tasks.celery_app import celery_app

        celery_app.send_task("food.broadcast_menu", args=[str(user.id), str(menu_id)])
        await show(query, "📣 ارسال پیام‌ها آغاز شد و در پس‌زمینه انجام می‌شود. نتیجه در گزارش ثبت می‌شود.")
    except Exception:
        log.warning("celery unavailable, broadcasting inline", exc_info=True)
        await show(query, "📣 در حال ارسال پیام‌ها…")
        result = await broadcast_menu(services, user.id, menu_id)
        await query_message(query).answer(
            f"✅ ارسال شد: {to_persian_digits(result.sent)} | ناموفق: {to_persian_digits(result.failed)}"
            f" | قبلاً ارسال‌شده: {to_persian_digits(result.skipped)}"
        )


# --- reports ------------------------------------------------------------------------------------------


@router.callback_query(FoodAdminCb.filter(F.a == "summary"))
async def summary(query: CallbackQuery, callback_data: FoodAdminCb, services: Services, user: User) -> None:
    offset = int(callback_data.v or 0)
    start, end = week_range(services.time.today() + timedelta(days=7 * offset))
    summaries = await services.food.day_summaries(user, start, end)
    title = f"گزارش سفارش غذا {format_date_numeric(start)} تا {format_date_numeric(end)}"
    await show(query, texts.day_summaries(summaries, title), inline([back_row("food")]))


@router.callback_query(FoodAdminCb.filter(F.a == "orders_xls"))
async def orders_excel(query: CallbackQuery, services: Services, user: User) -> None:
    start, end = week_range(services.time.today())
    rows = await services.food.detailed_rows(user, start, end + timedelta(days=7))
    await query.answer("در حال آماده‌سازی فایل…")
    summaries = await services.food.day_summaries(user, start, end + timedelta(days=7))
    message = query_message(query)
    await message.answer(texts.day_summaries(summaries, "خلاصه‌ی سفارش‌ها (این هفته و هفته بعد)"))
    await send_file(
        message, build_food_orders_workbook(rows), report_filename("food_orders", start, end + timedelta(days=7))
    )


@router.callback_query(FoodAdminCb.filter(F.a == "invoices"))
async def invoices(query: CallbackQuery, services: Services, user: User) -> None:
    invoices = await services.food.team_invoices(user)
    await query.answer("در حال آماده‌سازی فایل…")
    message = query_message(query)
    total = sum(i.total for i in invoices)
    currency = services.policy.food.currency_label
    await message.answer(
        f"🧾 صورت‌حساب هفتگی غذا\nتعداد افراد: {to_persian_digits(len(invoices))}\n"
        f"جمع کل: {to_persian_digits(f'{total:,}')} {currency}"
    )
    if invoices:
        start, end = invoices[0].week_start, invoices[0].week_end
        await send_file(
            message, build_invoices_workbook(invoices, currency), report_filename("food_invoices", start, end)
        )
