"""aiogram-based transport for Telegram and Telegram-compatible APIs (Bale, Soroush Plus)."""

import asyncio

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramNotFound,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from app.application.dto.messaging import DeliveryError, InlineButtons
from app.core.config.settings import Settings
from app.core.logging import get_logger

log = get_logger(__name__)

# Telegram allows 4096 characters; Bale and Soroush reject long texts with a generic 400.
MAX_MESSAGE_LENGTH = 3800
MAX_CAPTION_LENGTH = 900


# Set from settings by create_bot(); every inline keyboard is reflowed to this width.
_max_buttons_per_row = 8


def set_max_buttons_per_row(limit: int) -> None:
    global _max_buttons_per_row
    _max_buttons_per_row = max(1, limit)


def reflow(rows: list[list[InlineKeyboardButton]]) -> list[list[InlineKeyboardButton]]:
    """Split rows wider than the platform allows, keeping button order."""
    limit = _max_buttons_per_row
    out: list[list[InlineKeyboardButton]] = []
    for row in rows:
        out.extend(row[i : i + limit] for i in range(0, len(row), limit))
    return out


def create_bot(settings: Settings) -> Bot:
    """Create a Bot that talks to the configured platform's API server."""
    set_max_buttons_per_row(settings.max_buttons_per_row)
    session = AiohttpSession(api=TelegramAPIServer.from_base(settings.api_base_url))
    # No parse_mode and no link-preview options: Bale and Soroush implement an older subset of the
    # Telegram API and reject unknown fields with a generic 400 (INVALID INPUT).
    return Bot(token=settings.bot_token.get_secret_value(), session=session, default=DefaultBotProperties())


def to_markup(buttons: InlineButtons | None) -> InlineKeyboardMarkup | None:
    if not buttons or not buttons.rows:
        return None
    rows = [[InlineKeyboardButton(text=b.text, callback_data=b.callback_data) for b in row] for row in buttons.rows]
    return InlineKeyboardMarkup(inline_keyboard=reflow(rows))


def split_text(text: str, limit: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """Split on line boundaries so long reports never exceed the platform limit."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


class AiogramMessenger:
    def __init__(self, bot: Bot, max_retries: int = 3):
        self.bot = bot
        self.max_retries = max_retries

    async def send_text(self, chat_id: int, text: str, buttons: InlineButtons | None = None) -> None:
        chunks = split_text(text)
        for index, chunk in enumerate(chunks):
            # Buttons go on the last chunk so they sit under the end of the message.
            markup = to_markup(buttons) if index == len(chunks) - 1 else None
            await self._call(
                "sendMessage",
                chat_id,
                len(chunk),
                lambda c=chunk, m=markup: self.bot.send_message(chat_id=chat_id, text=c, reply_markup=m),
            )

    async def send_document(self, chat_id: int, filename: str, content: bytes, caption: str | None = None) -> None:
        document = BufferedInputFile(content, filename=filename)
        await self._call(
            "sendDocument",
            chat_id,
            len(content),
            lambda: self.bot.send_document(
                chat_id=chat_id, document=document, caption=(caption or "")[:MAX_CAPTION_LENGTH] or None
            ),
        )

    async def _call(self, method: str, chat_id: int, size: int, send) -> None:
        delay = 1.0
        for attempt in range(self.max_retries + 1):
            try:
                await send()
                return
            except TelegramRetryAfter as exc:
                log.warning("rate limited", method=method, chat_id=chat_id, retry_after=exc.retry_after)
                await asyncio.sleep(exc.retry_after)
            except (TelegramNetworkError, TelegramServerError) as exc:
                if attempt == self.max_retries:
                    raise DeliveryError(f"{method}: {exc}", permanent=False) from exc
                log.warning("temporary send failure", method=method, chat_id=chat_id, error=str(exc))
                await asyncio.sleep(delay)
                delay *= 2
            except (TelegramForbiddenError, TelegramNotFound, TelegramBadRequest) as exc:
                # Log the payload size, never the text: messages contain personal data.
                log.warning("permanent send failure", method=method, chat_id=chat_id, size=size, error=str(exc))
                raise DeliveryError(f"{method}: {exc}", permanent=True) from exc
        raise DeliveryError(f"{method}: retries exhausted", permanent=False)
