"""Helpers shared by handlers: editing the current message, sending files, generic picker navigation."""

import re
from datetime import date, time

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardMarkup, Message

from app.core.exceptions import ExpiredActionError
from app.core.utils.text import normalize_digits
from app.presentation.bot.callbacks.data import CalCb, TimeCb
from app.presentation.bot.keyboards.pickers import calendar, minute_picker

router = Router(name="pickers")

_TIME_RE = re.compile(r"^\s*(\d{1,2})\s*[:.٫]\s*(\d{2})\s*$")


def parse_hhmm(text: str) -> time | None:
    match = _TIME_RE.match(normalize_digits(text or ""))
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


async def show(query: CallbackQuery, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    """Replace the message the button belongs to; fall back to a new message if it can't be edited."""
    await query.answer()
    message = query.message
    if isinstance(message, Message):
        try:
            await message.edit_text(text, reply_markup=markup)
            return
        except Exception as exc:  # old messages, documents, or identical content
            if "message is not modified" in str(exc):
                return
        await message.answer(text, reply_markup=markup)


async def send_file(target: Message, content: bytes, filename: str, caption: str | None = None) -> None:
    await target.answer_document(BufferedInputFile(content, filename=filename), caption=caption)


def query_message(query: CallbackQuery) -> Message:
    if not isinstance(query.message, Message):
        raise ExpiredActionError()
    return query.message


async def start_picker(state: FSMContext, min_date: date, max_date: date) -> None:
    await state.update_data(cal_min=min_date.toordinal(), cal_max=max_date.toordinal())


async def picker_range(state: FSMContext) -> tuple[date, date]:
    data = await state.get_data()
    if "cal_min" not in data:
        raise ExpiredActionError()
    return date.fromordinal(data["cal_min"]), date.fromordinal(data["cal_max"])


@router.callback_query(CalCb.filter(F.a == "m"))
async def calendar_navigate(query: CallbackQuery, callback_data: CalCb, state: FSMContext) -> None:
    min_date, max_date = await picker_range(state)
    year, month = divmod(callback_data.v, 100)
    await query.answer()
    await query_message(query).edit_reply_markup(
        reply_markup=calendar(callback_data.p, year, month, min_date, max_date)
    )


@router.callback_query(TimeCb.filter(F.a == "h"))
async def hour_selected(query: CallbackQuery, callback_data: TimeCb, state: FSMContext) -> None:
    if await state.get_state() is None:
        raise ExpiredActionError()
    await query.answer()
    await query_message(query).edit_reply_markup(reply_markup=minute_picker(callback_data.p, callback_data.h))
