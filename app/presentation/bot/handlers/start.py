"""Registration, main menu, help, identity and global cancel."""

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from app.application.services.container import Services
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.security.rbac import ROLE_TITLES_FA
from app.core.utils.jalali import format_date
from app.core.utils.text import to_persian_digits
from app.domain.users.models import User
from app.presentation.bot.callbacks.data import NOOP, NavCb
from app.presentation.bot.keyboards.common import BTN_HELP
from app.presentation.bot.modules import HELP_TEXT, main_menu
from app.presentation.bot.states.flows import Registration

router = Router(name="start")

INACTIVE_TEXT = "حساب شما ثبت شده ولی هنوز فعال نشده است. لطفاً منتظر تأیید مدیر سیستم بمانید."


@router.message(Command("myid"))
async def my_id(message: Message, user: User | None) -> None:
    assert message.from_user is not None
    text = f"🆔 شناسه‌ی شما: {message.from_user.id}"
    if user is not None:
        text += f"\nنام ثبت‌شده: {user.full_name}"
    await message.answer(text)


@router.message(CommandStart())
async def start(message: Message, state: FSMContext, services: Services, user: User | None) -> None:
    assert message.from_user is not None
    # Failed staff-number attempts survive /start, so the lock can't be skipped by restarting.
    data = await state.get_data()
    await state.clear()
    if user is None:
        if services.users.roster_required(message.from_user.id):
            await state.set_state(Registration.employee_code)
            await state.update_data(
                roster_attempts=data.get("roster_attempts", 0), roster_lock_until=data.get("roster_lock_until")
            )
            await message.answer(
                "سلام! به ربات اداری خوش آمدید 👋\n"
                "برای ثبت‌نام، شماره موبایل (یا شماره پرسنلی) خود را که در فهرست کارکنان ثبت شده بنویسید.\n"
                "مثال: ۰۹۱۲۱۲۳۴۵۶۷",
                reply_markup=ReplyKeyboardRemove(),
            )
            return
        await state.set_state(Registration.full_name)
        await message.answer(
            "سلام! به ربات اداری خوش آمدید 👋\nبرای ثبت‌نام، لطفاً نام و نام خانوادگی خود را بنویسید:",
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    if not user.is_active:
        await message.answer(INACTIVE_TEXT)
        return
    await message.answer(f"سلام {user.full_name} 🌱\nیکی از گزینه‌های منو را انتخاب کنید.", reply_markup=main_menu(user))


@router.message(StateFilter(Registration.employee_code), F.text)
async def registration_code(message: Message, state: FSMContext, services: Services, user: User | None) -> None:
    assert message.from_user is not None and message.text is not None
    if user is not None:
        await state.clear()
        return
    policy = services.policy.users
    data = await state.get_data()
    now = services.time.now_utc().timestamp()
    lock_until = data.get("roster_lock_until") or 0
    if lock_until > now:
        minutes = int((lock_until - now) // 60) + 1
        await message.answer(f"⛔️ تعداد تلاش‌ها زیاد بود. {to_persian_digits(minutes)} دقیقه‌ی دیگر دوباره امتحان کنید.")
        return
    try:
        entry = await services.users.find_roster_entry(message.text)
    except (ValidationError, NotFoundError, ConflictError) as exc:
        attempts = data.get("roster_attempts", 0) + 1
        if attempts >= policy.max_roster_attempts:
            await state.update_data(roster_attempts=0, roster_lock_until=now + policy.roster_lock_minutes * 60)
            await message.answer(
                f"❌ {exc.user_message}\n⛔️ تعداد تلاش‌ها به حد مجاز رسید. "
                f"{to_persian_digits(policy.roster_lock_minutes)} دقیقه‌ی دیگر دوباره امتحان کنید."
            )
            return
        await state.update_data(roster_attempts=attempts)
        left = to_persian_digits(policy.max_roster_attempts - attempts)
        await message.answer(f"❌ {exc.user_message}\nدوباره بنویسید ({left} تلاش دیگر باقی است).")
        return
    if entry.full_name and services.users.is_valid_name(entry.full_name):
        await _finish_registration(message, state, services, None, entry.code)
        return
    await state.set_state(Registration.full_name)
    await state.update_data(roster_code=entry.code)
    await message.answer("✅ شماره تأیید شد.\nحالا نام و نام خانوادگی خود را بنویسید:")


@router.message(StateFilter(Registration.full_name), F.text)
async def registration_name(message: Message, state: FSMContext, services: Services, user: User | None) -> None:
    assert message.from_user is not None and message.text is not None
    if user is not None:
        await state.clear()
        return
    roster_code = (await state.get_data()).get("roster_code")
    await _finish_registration(message, state, services, message.text, roster_code)


async def _finish_registration(
    message: Message, state: FSMContext, services: Services, full_name: str | None, roster_code: str | None
) -> None:
    assert message.from_user is not None
    new_user = await services.users.register(
        message.from_user.id, message.chat.id, full_name, message.from_user.username, roster_code=roster_code
    )
    await state.clear()
    if not new_user.is_active:
        await message.answer(f"✅ ثبت‌نام شما با نام «{new_user.full_name}» انجام شد.\n{INACTIVE_TEXT}")
        return
    await message.answer(
        f"✅ ثبت‌نام شما با نام «{new_user.full_name}» انجام شد.\n\n{HELP_TEXT}", reply_markup=main_menu(new_user)
    )


@router.message(Command("cancel"))
@router.message(F.text.in_({"لغو", "/لغو"}))
async def cancel_command(message: Message, state: FSMContext, user: User | None) -> None:
    await state.clear()
    if user is not None and user.is_active:
        await message.answer("عملیات لغو شد.", reply_markup=main_menu(user))
    else:
        await message.answer("عملیات لغو شد. برای شروع /start را بزنید.")


@router.callback_query(NavCb.filter(F.to == "cancel"))
async def cancel_button(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await query.answer("لغو شد")
    if query.message is not None:
        await query.message.edit_text("عملیات لغو شد. از منوی پایین صفحه ادامه دهید.")  # type: ignore[union-attr]


@router.callback_query(NavCb.filter(F.to == "main"))
async def back_to_main(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await query.answer()
    if query.message is not None:
        await query.message.edit_text("منوی اصلی — از دکمه‌های پایین صفحه انتخاب کنید.")  # type: ignore[union-attr]


@router.callback_query(F.data == NOOP)
async def noop(query: CallbackQuery) -> None:
    await query.answer()


@router.message(Command("help"))
@router.message(F.text == BTN_HELP)
async def help_message(message: Message, user: User | None) -> None:
    if user is None:
        await message.answer("برای استفاده از ربات ابتدا /start را بزنید.")
        return
    await message.answer(HELP_TEXT, reply_markup=main_menu(user) if user.is_active else None)


@router.message(Command("whoami"))
async def whoami(message: Message, user: User | None, services: Services) -> None:
    if user is None:
        await message.answer("شما هنوز ثبت‌نام نکرده‌اید. /start را بزنید.")
        return
    roles = "، ".join(ROLE_TITLES_FA[r] for r in sorted(user.roles))
    await message.answer(
        f"👤 {user.full_name}\n🆔 {user.messenger_user_id}\nنقش‌ها: {roles}\n"
        f"وضعیت: {'فعال' if user.is_active else 'غیرفعال'}\n"
        f"تاریخ ثبت‌نام: {format_date(services.time.to_local(user.registered_at).date()) if user.registered_at else '—'}"
    )
