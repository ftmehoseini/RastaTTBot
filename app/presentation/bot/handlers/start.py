"""Registration, main menu, help, identity and global cancel."""

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from app.application.services.container import Services
from app.core.security.rbac import ROLE_TITLES_FA
from app.core.utils.jalali import format_date
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
async def start(message: Message, state: FSMContext, user: User | None) -> None:
    await state.clear()
    if user is None:
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


@router.message(StateFilter(Registration.full_name), F.text)
async def registration_name(message: Message, state: FSMContext, services: Services, user: User | None) -> None:
    assert message.from_user is not None and message.text is not None
    if user is not None:
        await state.clear()
        return
    new_user = await services.users.register(
        message.from_user.id, message.chat.id, message.text, message.from_user.username
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
