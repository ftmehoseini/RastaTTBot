from datetime import date, time, timedelta

import pytest
from sqlalchemy import func, select

from app.application.dto.attendance import CorrectionInput
from app.application.services.container import Services
from app.core.exceptions import ConflictError, ExpiredActionError, ValidationError
from app.core.utils.clock import FixedClock
from app.domain.attendance.models import (
    AttendanceDailySummary,
    AttendanceManualCorrection,
    AttendanceRecord,
    CorrectionType,
    DayStatus,
    RecordSource,
)
from app.domain.audit.models import AuditAction, AuditLog
from app.domain.users.models import SCHEDULE_HOURLY
from app.tests.conftest import tehran

MONDAY = date(2026, 10, 19)


async def punch_in(services: Services, user, clock: FixedClock):
    preview = await services.attendance.preview_check_in(user)
    return await services.attendance.check_in(user, preview.at_utc)


async def punch_out(services: Services, user, clock: FixedClock):
    preview = await services.attendance.preview_check_out(user)
    return await services.attendance.check_out(user, preview.at_utc, preview.work_date)


async def test_check_in_records_utc_and_local_work_date(services, make_user, clock) -> None:
    user = await make_user("علی رضایی")
    clock.set(tehran(2026, 10, 19, 9, 45))
    result = await punch_in(services, user, clock)
    assert result.created
    record = result.record
    assert record.work_date == MONDAY
    assert record.check_in_source == RecordSource.BOT_LIVE
    assert services.time.to_local(record.check_in_at).strftime("%H:%M") == "09:45"
    # Organization rules: 08:00–09:30 is floating (allowed), after 09:30 is unauthorized
    assert result.calculation is not None
    assert (result.calculation.floating_late_minutes, result.calculation.late_minutes) == (90, 15)
    audits = (await services.session.execute(select(AuditLog.action))).scalars().all()
    assert AuditAction.ATTENDANCE_CHECK_IN in audits


async def test_duplicate_check_in_shows_existing_instead_of_creating(services, make_user, clock) -> None:
    user = await make_user()
    first = await punch_in(services, user, clock)
    clock.advance(minutes=30)
    preview = await services.attendance.preview_check_in(user)
    assert preview.existing is not None and preview.existing.id == first.record.id
    # A stale confirmation button pressed again must not create a second record either.
    again = await services.attendance.check_in(user, preview.at_utc)
    assert not again.created
    count = (await services.session.execute(select(func.count(AttendanceRecord.id)))).scalar_one()
    assert count == 1


async def test_check_out_requires_check_in(services, make_user) -> None:
    user = await make_user()
    with pytest.raises(ConflictError):
        await services.attendance.preview_check_out(user)


async def test_check_out_computes_summary_and_blocks_duplicates(services, make_user, clock) -> None:
    user = await make_user()
    clock.set(tehran(2026, 10, 19, 8, 50))
    await punch_in(services, user, clock)
    clock.set(tehran(2026, 10, 19, 17, 40))
    result = await punch_out(services, user, clock)
    assert result.created
    assert result.calculation.gross_worked_minutes == 530
    summary = (await services.session.execute(select(AttendanceDailySummary))).scalar_one()
    assert summary.status == DayStatus.COMPLETE
    assert summary.overtime_minutes == 52  # in 08:50, out 17:40: presence after 16:48
    assert summary.floating_late_minutes == 50
    clock.advance(minutes=5)
    preview = await services.attendance.preview_check_out(user)
    assert preview.existing is not None  # shows the registered time instead
    again = await services.attendance.check_out(user, preview.at_utc, preview.work_date)
    assert not again.created


async def test_night_shift_check_out_after_midnight(services, make_user, clock) -> None:
    user = await make_user()
    clock.set(tehran(2026, 10, 19, 20, 0))
    await punch_in(services, user, clock)
    clock.set(tehran(2026, 10, 20, 3, 30))
    preview = await services.attendance.preview_check_out(user)
    assert preview.work_date == MONDAY
    result = await services.attendance.check_out(user, preview.at_utc, preview.work_date)
    assert result.record.work_date == MONDAY
    assert result.calculation.gross_worked_minutes == 450


async def test_expired_confirmation_is_rejected(services, make_user, clock) -> None:
    user = await make_user()
    preview = await services.attendance.preview_check_in(user)
    clock.advance(minutes=6)  # TTL is 5 minutes
    with pytest.raises(ExpiredActionError):
        await services.attendance.check_in(user, preview.at_utc)
    with pytest.raises(ExpiredActionError):
        await services.attendance.check_in(user, clock.now_utc() + timedelta(minutes=10))


# --- manual corrections -------------------------------------------------------------------------


async def test_forgotten_both_creates_manual_record_with_audit_trail(services, make_user, clock) -> None:
    user = await make_user()
    clock.set(tehran(2026, 10, 19, 12, 0))
    result = await services.attendance.submit_correction(
        user, CorrectionInput(CorrectionType.BOTH, date(2026, 10, 18), time(8, 30), time(16, 45))
    )
    record = result.record
    assert record.check_in_source == RecordSource.MANUAL_CORRECTION
    assert record.check_out_source == RecordSource.MANUAL_CORRECTION
    correction = (await services.session.execute(select(AttendanceManualCorrection))).scalar_one()
    assert correction.submitted_by == user.id
    assert correction.previous_check_in_at is None
    assert services.time.to_local(correction.claimed_check_out_at).strftime("%H:%M") == "16:45"


async def test_forgotten_check_out_after_live_check_in(services, make_user, clock) -> None:
    user = await make_user()
    clock.set(tehran(2026, 10, 18, 9, 0))
    await punch_in(services, user, clock)
    clock.set(tehran(2026, 10, 19, 10, 0))
    result = await services.attendance.submit_correction(
        user, CorrectionInput(CorrectionType.CHECK_OUT, date(2026, 10, 18), check_out_time=time(17, 15))
    )
    assert result.record.check_in_source == RecordSource.BOT_LIVE
    assert result.record.check_out_source == RecordSource.MANUAL_CORRECTION
    assert result.check_out_local.strftime("%H:%M") == "17:15"


async def test_correction_overnight_check_out(services, make_user, clock) -> None:
    user = await make_user()
    clock.set(tehran(2026, 10, 19, 12, 0))
    result = await services.attendance.submit_correction(
        user, CorrectionInput(CorrectionType.BOTH, date(2026, 10, 17), time(22, 0), time(5, 0))
    )
    assert result.check_out_local.date() == date(2026, 10, 18)


@pytest.mark.parametrize(
    ("kind", "day", "check_in", "check_out", "error"),
    [
        (CorrectionType.BOTH, date(2026, 10, 20), time(9, 0), time(17, 0), ValidationError),  # future date
        (CorrectionType.BOTH, date(2026, 9, 1), time(9, 0), time(17, 0), ValidationError),  # older than 30 days
        (CorrectionType.BOTH, date(2026, 10, 19), time(9, 0), time(17, 0), ValidationError),  # 17:00 today is future
        (CorrectionType.BOTH, date(2026, 10, 18), time(9, 0), time(9, 0), ValidationError),  # zero/24h shift
        (CorrectionType.CHECK_OUT, date(2026, 10, 18), None, time(17, 0), ConflictError),  # no check-in
        (CorrectionType.CHECK_IN, date(2026, 10, 18), None, None, ValidationError),  # time missing
    ],
)
async def test_correction_validation(services, make_user, clock, kind, day, check_in, check_out, error) -> None:
    user = await make_user()
    clock.set(tehran(2026, 10, 19, 12, 0))
    with pytest.raises(error):
        await services.attendance.submit_correction(user, CorrectionInput(kind, day, check_in, check_out))


async def test_correction_conflicts_with_existing_values(services, make_user, clock) -> None:
    user = await make_user()
    clock.set(tehran(2026, 10, 19, 9, 40))
    await punch_in(services, user, clock)
    clock.set(tehran(2026, 10, 19, 10, 0))
    # A forgotten entry is for earlier days; today's time goes through the manual (same-day) path.
    with pytest.raises(ValidationError):
        await services.attendance.submit_correction(
            user, CorrectionInput(CorrectionType.CHECK_IN, MONDAY, check_in_time=time(8, 15))
        )
    # Moving today's live check-in later is a conflict; moving it earlier fixes a late button press.
    with pytest.raises(ConflictError):
        await services.attendance.submit_correction(
            user, CorrectionInput(CorrectionType.CHECK_IN, MONDAY, check_in_time=time(9, 50), same_day=True)
        )
    result = await services.attendance.submit_correction(
        user, CorrectionInput(CorrectionType.CHECK_IN, MONDAY, check_in_time=time(8, 15), same_day=True)
    )
    assert result.record.check_in_source == RecordSource.MANUAL_TODAY
    with pytest.raises(ConflictError):
        await services.attendance.submit_correction(
            user, CorrectionInput(CorrectionType.BOTH, MONDAY, time(8, 0), time(9, 0), same_day=True)
        )


async def test_correction_cannot_overlap_next_day(services, make_user, clock) -> None:
    user = await make_user()
    clock.set(tehran(2026, 10, 19, 6, 0))
    await punch_in(services, user, clock)
    clock.set(tehran(2026, 10, 19, 12, 0))
    with pytest.raises(ConflictError):
        await services.attendance.submit_correction(
            user, CorrectionInput(CorrectionType.BOTH, date(2026, 10, 18), time(22, 0), time(7, 0))
        )


async def test_correction_requires_approval_when_configured(services, make_user, clock, policy) -> None:
    policy.attendance.manual_correction_requires_approval = True
    user = await make_user()
    clock.set(tehran(2026, 10, 19, 12, 0))
    result = await services.attendance.submit_correction(
        user, CorrectionInput(CorrectionType.BOTH, date(2026, 10, 18), time(9, 0), time(17, 0))
    )
    assert result.status.value == "PENDING_APPROVAL"
    assert result.record.check_in_at is None  # not applied until approved


# --- reports ----------------------------------------------------------------------------------------


async def test_weekly_report_marks_absent_days_and_totals(services, make_user, clock) -> None:
    user = await make_user()
    clock.set(tehran(2026, 10, 19, 12, 0))
    await services.attendance.submit_correction(
        user, CorrectionInput(CorrectionType.BOTH, date(2026, 10, 17), time(9, 0), time(18, 0))
    )
    report = await services.attendance.my_week(user)
    statuses = {r.work_date: r.calculation.status for r in report.rows}
    assert statuses[date(2026, 10, 17)] == DayStatus.COMPLETE
    assert statuses[date(2026, 10, 18)] == DayStatus.ABSENT
    assert MONDAY not in statuses  # today without a record is not shown yet
    assert report.totals.present_days == 1 and report.totals.absent_days == 1
    assert report.totals.overtime_minutes == 72  # 09:00–18:00: presence after 16:48
    assert report.totals.floating_late_minutes == 60
    assert report.rows[0].is_manual


async def test_monthly_report_carries_caps(services, make_user, clock) -> None:
    user = await make_user()
    clock.set(tehran(2026, 10, 19, 12, 0))
    await services.attendance.submit_correction(
        user, CorrectionInput(CorrectionType.BOTH, date(2026, 10, 18), time(10, 0), time(16, 0))
    )
    month = await services.attendance.my_month(user)
    assert (month.late_cap_minutes, month.early_cap_minutes) == (420, 180)
    assert month.totals.late_minutes >= 30 and month.totals.early_departure_minutes >= 48
    week = await services.attendance.my_week(user)
    assert (week.late_cap_minutes, week.early_cap_minutes) == (0, 0)  # caps are monthly only


async def test_hourly_staff_only_track_worked_time(services, make_user, clock) -> None:
    hourly = await make_user("نیروی ساعتی")
    hourly.schedule_code = SCHEDULE_HOURLY
    clock.set(tehran(2026, 10, 19, 12, 0))
    await services.attendance.submit_correction(
        hourly, CorrectionInput(CorrectionType.BOTH, date(2026, 10, 18), time(10, 0), time(14, 30))
    )
    week = await services.attendance.my_week(hourly)
    assert [r.work_date for r in week.rows] == [date(2026, 10, 18)]  # no "absent" days for hourly staff
    c = week.rows[0].calculation
    assert c.hourly and c.gross_worked_minutes == 270
    assert (c.floating_late_minutes, c.late_minutes, c.early_departure_minutes, c.overtime_minutes) == (0, 0, 0, 0)
    assert c.deficit_minutes == 0 and week.totals.absent_days == 0
    month = await services.attendance.my_month(hourly)
    assert month.hourly and (month.late_cap_minutes, month.early_cap_minutes) == (0, 0)
    full_time = await make_user("نیروی تمام‌وقت")
    recipients = await services.attendance.check_in_reminder_recipients(MONDAY)
    assert full_time in recipients and hourly in recipients  # hourly staff get the reminders too


async def test_month_report_follows_the_excel_summary(services, make_user, clock) -> None:
    from app.domain.leave.models import LeavePayType, LeaveRequest, LeaveStatus, LeaveUnit

    user = await make_user()
    clock.set(tehran(2026, 10, 19, 20, 0))
    # 18/10: in 10:00 → 0:30 unauthorized late; out 18:00 → 1:12 overtime
    await services.attendance.submit_correction(
        user, CorrectionInput(CorrectionType.BOTH, date(2026, 10, 18), time(10, 0), time(18, 0))
    )
    # 17/10: in 08:30, out 16:00 → 0:30 allowed late, 0:48 early departure
    await services.attendance.submit_correction(
        user, CorrectionInput(CorrectionType.BOTH, date(2026, 10, 17), time(8, 30), time(16, 0))
    )
    services.session.add(
        LeaveRequest(
            user_id=user.id,
            pay_type=LeavePayType.PAID,
            unit=LeaveUnit.HOURLY,
            start_date=date(2026, 10, 18),
            end_date=date(2026, 10, 18),
            start_time=time(16, 0),
            end_time=time(18, 0),
            starts_at=tehran(2026, 10, 18, 16, 0),
            ends_at=tehran(2026, 10, 18, 18, 0),
            duration_minutes=120,
            status=LeaveStatus.APPROVED,
            submitted_at=tehran(2026, 10, 18, 9, 0),
        )
    )
    await services.session.commit()
    month = await services.attendance.my_month(user)
    m = month.sheet
    assert (m.floating_late, m.late, m.early, m.overtime, m.paid_leave) == (30 + 90, 30, 48, 72, 120)
    assert m.compensable == 120 + 30 + 48
    assert m.uncompensated == 198 - 72
    assert m.final_work == round(m.normal + 72 + 120 - 126 * 1.6)
    row = next(r for r in month.rows if r.work_date == date(2026, 10, 18))
    assert row.paid_leave_minutes == 120 and "مرخصی ساعتی با حقوق" in row.leave_note


async def test_edit_a_registered_time(services, make_user, clock) -> None:
    from app.application.dto.attendance import EditInput

    user = await make_user()
    clock.set(tehran(2026, 10, 19, 9, 45))
    await punch_in(services, user, clock)
    clock.set(tehran(2026, 10, 19, 17, 0))
    await punch_out(services, user, clock)
    clock.set(tehran(2026, 10, 19, 18, 0))
    edit = services.attendance.edit_entry
    with pytest.raises(ValidationError):  # check-in after the check-out
        await edit(user, EditInput(MONDAY, "in", time(17, 30)))
    with pytest.raises(ValidationError):  # nothing changes
        await edit(user, EditInput(MONDAY, "in", time(9, 45)))
    with pytest.raises(ValidationError):  # in the future
        await edit(user, EditInput(MONDAY, "out", time(19, 0)))
    with pytest.raises(ConflictError):  # no record that day: that is a forgotten entry
        await edit(user, EditInput(date(2026, 10, 18), "in", time(8, 0)))
    with pytest.raises(ValidationError):  # too old
        await edit(user, EditInput(MONDAY - timedelta(days=40), "in", time(8, 0)))

    result = await edit(user, EditInput(MONDAY, "in", time(8, 40)))
    assert result.record.check_in_source == RecordSource.USER_EDIT
    assert services.time.to_local(result.record.check_in_at).strftime("%H:%M") == "08:40"
    summary = (await services.session.execute(select(AttendanceDailySummary))).scalar_one()
    assert (summary.late_minutes, summary.floating_late_minutes) == (0, 40)  # recalculated
    trail = (await services.session.execute(select(AttendanceManualCorrection))).scalar_one()
    assert services.time.to_local(trail.previous_check_in_at).strftime("%H:%M") == "09:45"  # history kept
    week = await services.attendance.my_week(user)
    assert week.rows[-1].is_edited
