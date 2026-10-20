"""Sends messages through the Messenger port and records every attempt in notification_logs."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.dto.messaging import DeliveryError, InlineButtons, Messenger
from app.core.config.policy import NotificationPolicy
from app.core.logging import get_logger
from app.core.utils.clock import TimeService
from app.domain.notifications.models import NotificationLog, NotificationStatus, NotificationType
from app.domain.users.models import User
from app.infrastructure.repositories.notifications import NotificationRepository

log = get_logger(__name__)


class SendOutcome(StrEnum):
    SENT = "SENT"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"  # the dedup key was already sent (or is being sent) by another run


@dataclass(frozen=True)
class BulkResult:
    sent: int = 0
    failed: int = 0
    skipped: int = 0

    def add(self, outcome: SendOutcome) -> "BulkResult":
        return BulkResult(
            self.sent + (outcome == SendOutcome.SENT),
            self.failed + (outcome == SendOutcome.FAILED),
            self.skipped + (outcome == SendOutcome.SKIPPED),
        )


class NotificationService:
    """`notify` commits the session: commit business changes before notifying."""

    def __init__(self, session: AsyncSession, messenger: Messenger, time: TimeService, policy: NotificationPolicy):
        self.session = session
        self.messenger = messenger
        self.time = time
        self.policy = policy
        self.logs = NotificationRepository(session)

    async def notify(
        self,
        chat_id: int,
        text: str,
        notification_type: NotificationType = NotificationType.GENERIC,
        *,
        user_id: UUID | None = None,
        buttons: InlineButtons | None = None,
        dedup_key: str | None = None,
        batch_key: str | None = None,
    ) -> SendOutcome:
        if dedup_key is not None:
            entry = await self.logs.claim(
                dedup_key, user_id, chat_id, notification_type, batch_key, self.policy.max_send_retries + 1
            )
            if entry is None:
                return SendOutcome.SKIPPED
        else:
            entry = self.logs.add(
                NotificationLog(
                    user_id=user_id,
                    chat_id=chat_id,
                    notification_type=notification_type,
                    batch_key=batch_key,
                    status=NotificationStatus.PENDING,
                    attempts=0,
                )
            )
        # Commit the claim before sending, so a crash mid-send can never cause a second send.
        await self.session.commit()

        entry.attempts = (entry.attempts or 0) + 1
        try:
            await self.messenger.send_text(chat_id, text, buttons)
        except DeliveryError as exc:
            entry.status = NotificationStatus.FAILED
            entry.error = str(exc)[:500]
            await self.session.commit()
            log.warning("notification failed", type=notification_type.value, chat_id=chat_id, permanent=exc.permanent)
            return SendOutcome.FAILED
        entry.status = NotificationStatus.SENT
        entry.sent_at = self.time.now_utc()
        entry.error = None
        await self.session.commit()
        return SendOutcome.SENT

    async def notify_user(
        self, user: User, text: str, notification_type: NotificationType = NotificationType.GENERIC, **kwargs
    ) -> SendOutcome:
        return await self.notify(user.chat_id, text, notification_type, user_id=user.id, **kwargs)

    async def notify_many(
        self,
        users: Sequence[User],
        text: str,
        notification_type: NotificationType,
        *,
        dedup_prefix: str,
        batch_key: str | None = None,
        buttons: InlineButtons | None = None,
    ) -> BulkResult:
        """Rate-limited bulk send. Re-running with the same prefix resumes without duplicates."""
        result = BulkResult()
        for index, user in enumerate(users):
            if index and index % self.policy.batch_size == 0:
                await asyncio.sleep(self.policy.batch_interval_seconds)
            outcome = await self.notify_user(
                user,
                text,
                notification_type,
                buttons=buttons,
                dedup_key=f"{dedup_prefix}:{user.id}",
                batch_key=batch_key,
            )
            result = result.add(outcome)
        return result

    async def send_document(self, chat_id: int, filename: str, content: bytes, caption: str | None = None) -> bool:
        try:
            await self.messenger.send_document(chat_id, filename, content, caption)
        except DeliveryError as exc:
            log.warning("document delivery failed", chat_id=chat_id, permanent=exc.permanent)
            return False
        return True
