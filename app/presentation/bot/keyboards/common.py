"""Shared labels and keyboards. Module menus are registered in `presentation/bot/modules.py`."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from app.infrastructure.integrations.messenger import reflow
from app.presentation.bot.callbacks.data import NOOP, NavCb

BTN_HELP = "ℹ️ راهنما"
BTN_BACK = "🔙 بازگشت"
BTN_CANCEL = "✖️ لغو"
BTN_CONFIRM = "✅ تأیید"
BTN_STEP_BACK = "🔙 مرحله قبل"


def button(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def inline(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    # Wide rows (hour/minute pickers) are split to the platform's limit, e.g. 2 on Soroush.
    return InlineKeyboardMarkup(inline_keyboard=reflow(rows))


def back_row(to: str = "main") -> list[InlineKeyboardButton]:
    return [button(BTN_BACK, NavCb(to=to).pack())]


def cancel_row() -> list[InlineKeyboardButton]:
    return [button(BTN_CANCEL, NavCb(to="cancel").pack())]


def step_row(back_data: str | None) -> list[InlineKeyboardButton]:
    """Cancel plus "previous step" for wizard steps. Soroush draws rows left to right, so the
    button listed last ends up on the right, where Persian readers look first."""
    if back_data is None:
        return cancel_row()
    return [button(BTN_CANCEL, NavCb(to="cancel").pack()), button(BTN_STEP_BACK, back_data)]


def confirm_rows(confirm_data: str, back_data: str | None = None) -> list[list[InlineKeyboardButton]]:
    rows = [[button(BTN_CONFIRM, confirm_data), button(BTN_CANCEL, NavCb(to="cancel").pack())]]
    if back_data is not None:
        rows.append([button(BTN_STEP_BACK, back_data)])
    return rows


def noop(text: str) -> InlineKeyboardButton:
    return button(text, NOOP)


def chunk(buttons: list[InlineKeyboardButton], size: int) -> list[list[InlineKeyboardButton]]:
    return [buttons[i : i + size] for i in range(0, len(buttons), size)]


def reply_menu(rows: list[list[str]]) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=label) for label in row] for row in rows],
        resize_keyboard=True,
    )
