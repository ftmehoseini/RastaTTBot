from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.domain.notifications.models import NotificationLog, NotificationStatus, NotificationType
from app.infrastructure.repositories.base import Repository


class NotificationRepository(Repository[NotificationLog]):
    model = NotificationLog

    async def claim(
        self,
        dedup_key: str,
        user_id: UUID | None,
        chat_id: int,
        notification_type: NotificationType,
        batch_key: str | None = None,
        max_attempts: int = 4,
    ) -> NotificationLog | None:
        """Insert a PENDING log row. Returns None if another worker already claimed `dedup_key`.

        Uses a savepoint so a duplicate does not roll back the caller's transaction.
        """
        existing = await self.get_by_key(dedup_key)
        if existing is not None:
            # A failed attempt may be retried (up to max_attempts); a sent or in-flight one may not.
            retryable = existing.status == NotificationStatus.FAILED and existing.attempts < max_attempts
            return existing if retryable else None
        log = NotificationLog(
            dedup_key=dedup_key,
            user_id=user_id,
            chat_id=chat_id,
            notification_type=notification_type,
            batch_key=batch_key,
            status=NotificationStatus.PENDING,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(log)
        except IntegrityError:
            return None
        return log

    async def get_by_key(self, dedup_key: str) -> NotificationLog | None:
        stmt = select(NotificationLog).where(NotificationLog.dedup_key == dedup_key)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def batch_counts(self, batch_key: str) -> dict[NotificationStatus, int]:
        stmt = (
            select(NotificationLog.status, func.count(NotificationLog.id))
            .where(NotificationLog.batch_key == batch_key)
            .group_by(NotificationLog.status)
        )
        return {status: count for status, count in (await self.session.execute(stmt)).all()}
