"""Helpers shared by handlers: editing the current message, sending files, generic picker navigation."""

import re
from datetime import date, time

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardMarkup, Message

from app.core.exceptions import ExpiredActionError
from app.core.logging import get_logger
from app.core.utils.jalali import JalaliDate
from app.core.utils.text import normalize_digits
from app.presentation.bot.callbacks.data import CalCb, TimeCb
from app.presentation.bot.keyboards.pickers import day_page, minute_picker

log = get_logger(__name__)

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
        except Exception as exc:  # old messages, documents, identical content, unsupported edits
            if "message is not modified" in str(exc):
                return
            log.warning("edit failed, sending a new message", error=str(exc))
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


async def reply(target: CallbackQuery | Message, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    """Answer a button press (by editing its message) or a typed message (with a new message)."""
    if isinstance(target, CallbackQuery):
        await show(target, text, markup)
    else:
        await target.answer(text, reply_markup=markup)


def parse_typed_date(text: str) -> date | None:
    try:
        return JalaliDate.parse(text).to_gregorian()
    except ValueError:
        return None


# Navigation re-sends the text with the new keyboard (edit_text) rather than edit_reply_markup,
# which not every Telegram-compatible platform implements.


@router.callback_query(CalCb.filter(F.a == "m"))
async def calendar_navigate(query: CallbackQuery, callback_data: CalCb, state: FSMContext) -> None:
    min_date, max_date = await picker_range(state)
    page = day_page(callback_data.p, date.fromordinal(callback_data.v), min_date, max_date)
    await show(query, query_message(query).text or "📅 تاریخ را انتخاب کنید:", page)


@router.callback_query(TimeCb.filter(F.a == "h"))
async def hour_selected(query: CallbackQuery, callback_data: TimeCb, state: FSMContext) -> None:
    if await state.get_state() is None:
        raise ExpiredActionError()
    await show(
        query, query_message(query).text or "🕘 دقیقه را انتخاب کنید:", minute_picker(callback_data.p, callback_data.h)
    )
