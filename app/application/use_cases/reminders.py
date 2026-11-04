"""Daily attendance reminders. Safe to run repeatedly and on several workers at once."""

from dataclasses import dataclass

from app.application.dto.messaging import InlineButtons
from app.application.presenters import attendance as texts
from app.application.services.container import Services
from app.application.services.notification_service import BulkResult
from app.core.logging import get_logger
from app.domain.notifications.models import NotificationType

log = get_logger(__name__)

# Buttons under the reminders. The attendance router handles exactly these strings (imported from
# here), so a reminder button can't point at something nobody answers.
REMINDER_CHECK_IN = "open:att:in"  # opens today's check-in
REMINDER_CHECK_OUT = "open:att:out"  # opens today's check-out
# Sent by older versions; still in people's chats.
LEGACY_REMINDER_BUTTON = "att:menu"


@dataclass(frozen=True)
class ReminderRun:
    due: bool
    sent: int = 0
    failed: int = 0
    skipped: int = 0


async def send_check_in_reminders(services: Services, force: bool = False) -> ReminderRun:
    """`force` (manual run by an operator) ignores the clock; holidays and "already reminded today" still apply."""
    policy = services.policy.attendance
    attendance = services.attendance
    today = services.time.today()
    # Holidays are off for everyone; weekdays are checked per person (some work on Thursday).
    if attendance.is_holiday(today) or not (force or attendance.reminder_due(policy.check_in_reminder_time)):
        return ReminderRun(due=False)
    recipients = await attendance.check_in_reminder_recipients(today)
    result = await services.notifications.notify_many(
        recipients,
        texts.check_in_reminder(today),
        NotificationType.CHECK_IN_REMINDER,
        dedup_prefix=f"check_in_reminder:{today.isoformat()}",
        batch_key=f"check_in_reminder:{today.isoformat()}",
        buttons=InlineButtons.of([("⏱ ثبت ورود امروز", REMINDER_CHECK_IN)]),
    )
    log.info(
        "check-in reminders", day=today.isoformat(), sent=result.sent, failed=result.failed, skipped=result.skipped
    )
    return ReminderRun(True, result.sent, result.failed, result.skipped)


async def send_check_out_reminders(services: Services, force: bool = False) -> ReminderRun:
    policy = services.policy.attendance
    attendance = services.attendance
    today = services.time.today()
    if not (force or attendance.reminder_due(policy.check_out_reminder_time)):
        return ReminderRun(due=False)
    result = BulkResult()
    for user, record in await attendance.check_out_reminder_recipients(today):
        outcome = await services.notifications.notify_user(
            user,
            texts.check_out_reminder(today, services.time.to_local(record.check_in_at)),
            NotificationType.CHECK_OUT_REMINDER,
            dedup_key=f"check_out_reminder:{today.isoformat()}:{user.id}",
            batch_key=f"check_out_reminder:{today.isoformat()}",
            buttons=InlineButtons.of([("🏁 ثبت خروج امروز", REMINDER_CHECK_OUT)]),
        )
        result = result.add(outcome)
    log.info("check-out reminders", day=today.isoformat(), sent=result.sent, failed=result.failed)
    return ReminderRun(True, result.sent, result.failed, result.skipped)


async def send_test_reminder(services: Services, chat_id: int, kind: str) -> None:
    """Send one reminder, exactly as people receive it, to one chat. Not logged, no dedup: for testing."""
    today = services.time.today()
    if kind == "in":
        text, button = texts.check_in_reminder(today), ("⏱ ثبت ورود امروز", REMINDER_CHECK_IN)
    else:
        text = texts.check_out_reminder(today, services.time.now_local())
        button = ("🏁 ثبت خروج امروز", REMINDER_CHECK_OUT)
    await services.messenger.send_text(chat_id, "🧪 پیام آزمایشی\n" + text, InlineButtons.of([button]))
