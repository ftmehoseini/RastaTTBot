"""Shared labels and keyboards. Module menus are registered in `presentation/bot/modules.py`."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from app.presentation.bot.callbacks.data import NOOP, NavCb

BTN_HELP = "ℹ️ راهنما"
BTN_BACK = "🔙 بازگشت"
BTN_CANCEL = "✖️ لغو"
BTN_CONFIRM = "✅ تأیید"


def button(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def inline(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_row(to: str = "main") -> list[InlineKeyboardButton]:
    return [button(BTN_BACK, NavCb(to=to).pack())]


def cancel_row() -> list[InlineKeyboardButton]:
    return [button(BTN_CANCEL, NavCb(to="cancel").pack())]


def confirm_rows(confirm_data: str) -> list[list[InlineKeyboardButton]]:
    return [[button(BTN_CONFIRM, confirm_data), button(BTN_CANCEL, NavCb(to="cancel").pack())]]


def noop(text: str) -> InlineKeyboardButton:
    return button(text, NOOP)


def chunk(buttons: list[InlineKeyboardButton], size: int) -> list[list[InlineKeyboardButton]]:
    return [buttons[i : i + size] for i in range(0, len(buttons), size)]


def reply_menu(rows: list[list[str]]) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=label) for label in row] for row in rows],
        resize_keyboard=True,
        is_persistent=True,
    )
