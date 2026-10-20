"""Last router: unregistered users, stale buttons and unknown text."""

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.domain.users.models import User
from app.presentation.bot.modules import main_menu

router = Router(name="fallback")


@router.callback_query()
async def stale_callback(query: CallbackQuery, user: User | None) -> None:
    if user is None:
        await query.answer("ابتدا با /start ثبت‌نام کنید.", show_alert=True)
    elif not user.is_active:
        await query.answer("حساب شما فعال نیست.", show_alert=True)
    else:
        await query.answer("این دکمه منقضی شده است. لطفاً دوباره از منو شروع کنید.", show_alert=True)


@router.message()
async def unknown_message(message: Message, state: FSMContext, user: User | None) -> None:
    if user is None:
        await message.answer("سلام! برای استفاده از ربات ابتدا /start را بزنید و ثبت‌نام کنید.")
        return
    if not user.is_active:
        await message.answer("حساب شما هنوز فعال نشده است. لطفاً منتظر تأیید مدیر سیستم بمانید.")
        return
    if await state.get_state() is not None:
        await message.answer("لطفاً از دکمه‌های همین مرحله استفاده کنید یا /cancel را بزنید.")
        return
    await message.answer("لطفاً یکی از گزینه‌های منوی پایین صفحه را انتخاب کنید.", reply_markup=main_menu(user))
