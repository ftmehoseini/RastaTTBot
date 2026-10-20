from datetime import date, time

from sqlalchemy import select

from app.application.dto.leave import LeaveDraft
from app.application.use_cases.reminders import send_check_in_reminders, send_check_out_reminders
from app.core.security.rbac import RoleCode
from app.domain.approvals.engine import Decision
from app.domain.leave.models import LeavePayType, LeaveUnit
from app.domain.notifications.models import NotificationLog, NotificationStatus
from app.tests.conftest import tehran


async def test_check_in_reminder_selects_only_missing_employees(services, make_user, clock, messenger) -> None:
    present = await make_user("حاضر")
    absent = await make_user("غایب")
    inactive = await make_user("غیرفعال", active=False)
    on_leave = await make_user("مرخصی")
    secretary = await make_user("منشی", RoleCode.ADMIN_1_SECRETARY)
    manager = await make_user("مدیر", RoleCode.ADMIN_2_MANAGER)

    clock.set(tehran(2026, 10, 18, 9, 0))
    draft = LeaveDraft(LeavePayType.PAID, LeaveUnit.DAILY, date(2026, 10, 19), date(2026, 10, 19))
    submitted = await services.leave.submit(on_leave, draft)
    await services.leave.decide(secretary, submitted.request.id, 1, Decision.APPROVE)
    await services.leave.decide(manager, submitted.request.id, 2, Decision.APPROVE)

    clock.set(tehran(2026, 10, 19, 9, 0))
    preview = await services.attendance.preview_check_in(present)
    await services.attendance.check_in(present, preview.at_utc)

    recipients = await services.attendance.check_in_reminder_recipients(date(2026, 10, 19))
    ids = {u.id for u in recipients}
    assert absent.id in ids
    assert present.id not in ids
    assert inactive.id not in ids
    assert on_leave.id not in ids


async def test_check_in_reminder_is_idempotent_and_respects_time(services, make_user, clock, messenger) -> None:
    user = await make_user()
    clock.set(tehran(2026, 10, 19, 9, 50))
    assert not (await send_check_in_reminders(services)).due  # before 10:00

    clock.set(tehran(2026, 10, 19, 10, 0))
    first = await send_check_in_reminders(services)
    assert first.due and first.sent == 1
    clock.set(tehran(2026, 10, 19, 10, 10))
    second = await send_check_in_reminders(services)
    assert second.sent == 0 and second.skipped == 1
    assert len(messenger.texts_to(user.chat_id)) == 1

    clock.set(tehran(2026, 10, 19, 13, 1))  # catch-up window (180 min) is over
    assert not (await send_check_in_reminders(services)).due


async def test_no_reminders_on_non_workdays(services, make_user, clock, messenger) -> None:
    await make_user()
    clock.set(tehran(2026, 10, 23, 10, 5))  # Friday
    assert not (await send_check_in_reminders(services)).due
    assert messenger.sent == []


async def test_check_out_reminder_only_for_open_records(services, make_user, clock, messenger) -> None:
    still_in = await make_user("هنوز داخل")
    left = await make_user("رفته")
    clock.set(tehran(2026, 10, 19, 9, 0))
    for user in (still_in, left):
        preview = await services.attendance.preview_check_in(user)
        await services.attendance.check_in(user, preview.at_utc)
    clock.set(tehran(2026, 10, 19, 17, 0))
    preview = await services.attendance.preview_check_out(left)
    await services.attendance.check_out(left, preview.at_utc, preview.work_date)

    clock.set(tehran(2026, 10, 19, 20, 5))
    run = await send_check_out_reminders(services)
    assert run.sent == 1
    assert messenger.texts_to(still_in.chat_id) and not messenger.texts_to(left.chat_id)
    assert (await send_check_out_reminders(services)).sent == 0


async def test_failed_delivery_is_logged_and_retried_later(services, make_user, clock, messenger) -> None:
    user = await make_user()
    messenger.fail_chats.add(user.chat_id)
    clock.set(tehran(2026, 10, 19, 10, 0))
    run = await send_check_in_reminders(services)
    assert run.failed == 1
    log = (await services.session.execute(select(NotificationLog))).scalar_one()
    assert log.status == NotificationStatus.FAILED and log.attempts == 1

    messenger.fail_chats.clear()
    clock.set(tehran(2026, 10, 19, 10, 10))
    run = await send_check_in_reminders(services)
    assert run.sent == 1
    await services.session.refresh(log)
    assert log.status == NotificationStatus.SENT and log.attempts == 2


def test_reminder_window(services, clock) -> None:
    clock.set(tehran(2026, 10, 19, 20, 0))
    assert services.attendance.reminder_due(time(20, 0))
    assert not services.attendance.reminder_due(time(20, 1))
