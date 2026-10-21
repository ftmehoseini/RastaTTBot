"""Messenger-agnostic outgoing message types.

Services and background jobs talk to `Messenger`, never to aiogram, so business logic does
not change when the transport (Telegram, Bale, Soroush, a test fake) changes.
"""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Button:
    text: str
    callback_data: str


@dataclass(frozen=True)
class InlineButtons:
    rows: list[list[Button]] = field(default_factory=list)

    @classmethod
    def of(cls, *rows: list[tuple[str, str]]) -> "InlineButtons":
        return cls([[Button(text, data) for text, data in row] for row in rows])


class DeliveryError(Exception):
    """Sending failed. `permanent` means retrying will not help (blocked bot, bad chat ID)."""

    def __init__(self, message: str, permanent: bool):
        super().__init__(message)
        self.permanent = permanent


class Messenger(Protocol):
    async def send_text(self, chat_id: int, text: str, buttons: InlineButtons | None = None) -> None: ...

    async def send_document(self, chat_id: int, filename: str, content: bytes, caption: str | None = None) -> None: ...
