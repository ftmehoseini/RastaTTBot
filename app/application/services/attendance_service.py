"""Attendance rules: live check-in/out, forgotten-entry corrections, daily calculation and reports."""

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.dto.attendance import (
    CorrectionInput,
    CorrectionResult,
    DayRow,
    EditInput,
    EmployeeReport,
    PunchPreview,
    PunchResult,
    TeamSummary,
    Totals,
)
from app.application.services import access
from app.core.config.policy import OrganizationPolicy, Policy
from app.core.exceptions import ConflictError, ExpiredActionError, NotFoundError, ValidationError
from app.core.security.rbac import Permission
from app.core.utils.clock import TimeService, ensure_utc
from app.core.utils.jalali import (
    JalaliDate,
    format_date,
    format_datetime,
    format_time,
    iter_days,
    month_range,
    week_range,
)
from app.core.utils.text import format_minutes
from app.domain.attendance.calculation import (
    HOURLY_POLICY,
    DailyCalculation,
    ScheduleResolver,
    get_calculation_policy,
)
from app.domain.attendance.iran_holidays import IRAN_OFFICIAL_HOLIDAYS
from app.domain.attendance.models import (
    AttendanceDailySummary,
    AttendanceManualCorrection,
    AttendanceRecord,
    CorrectionStatus,
    CorrectionType,
    DayStatus,
    RecordSource,
)
from app.domain.attendance.timesheet import SheetRules, sheet_month
from app.domain.audit.models import AuditAction
from app.domain.leave.models import LeavePayType, LeaveRequest, LeaveStatus, LeaveUnit
from app.domain.users.models import User
from app.infrastructure.repositories.attendance import AttendanceRepository
from app.infrastructure.repositories.audit import AuditRepository
from app.infrastructure.repositories.leave import LeaveRepository
from app.infrastructure.repositories.users import UserRepository

# A confirmed time may be at most this far in the future (clock skew between workers).
_CLOCK_SKEW = timedelta(minutes=1)


@dataclass(frozen=True)
class _CorrectionPlan:
    record: AttendanceRecord | None
    now: datetime
    needs_in: bool
    needs_out: bool
    current_in: datetime | None
    current_out: datetime | None
    new_in: datetime | None
    new_out: datetime | None


def parse_holidays(values: list[str]) -> set[date]:
    return {JalaliDate.parse(v).to_gregorian() for v in values}


def organization_holidays(org: OrganizationPolicy) -> dict[date, str]:
    """Days off for everyone: Iran's official holidays (unless excepted) plus the organization's own."""
    days = dict(IRAN_OFFICIAL_HOLIDAYS) if org.official_holidays else {}
    for day in parse_holidays(org.holiday_exceptions):
        days.pop(day, None)
    for day in parse_holidays(org.holidays):
        days.setdefault(day, "تعطیل سازمان")
    return days


class AttendanceService:
    def __init__(self, session: AsyncSession, policy: Policy, time: TimeService):
        self.session = session
        self.policy = policy.attendance
        self.leave_policy = policy.leave
        self.time = time
        self.records = AttendanceRepository(session)
        self.users = UserRepository(session)
        self.leaves = LeaveRepository(session)
        self.audit = AuditRepository(session)
        self.schedules = ScheduleResolver(self.policy, organization_holidays(policy.organization))
        self.calculator = get_calculation_policy(self.policy.calculation_policy)

    @property
    def max_shift(self) -> timedelta:
        return timedelta(hours=self.policy.max_shift_hours)

    # --- queries ---------------------------------------------------------------

    async def record_for(self, user: User, work_date: date) -> AttendanceRecord | None:
        return await self.records.get_for_day(user.id, work_date)

    async def open_night_shift(self, user: User) -> AttendanceRecord | None:
        """Yesterday's record still open and within the maximum shift length."""
        yesterday = await self.records.get_for_day(user.id, self.time.today() - timedelta(days=1))
        if (
            yesterday
            and yesterday.check_in_at
            and not yesterday.check_out_at
            and self.time.now_utc() - ensure_utc(yesterday.check_in_at) <= self.max_shift
        ):
            return yesterday
        return None

    # --- live check-in -----------------------------------------------------------

    async def preview_check_in(self, user: User) -> PunchPreview:
        access.require(user, Permission.ATTENDANCE_SELF)
        now = self.time.now_utc().replace(microsecond=0)
        today = self.time.today()
        existing = await self.records.get_for_day(user.id, today)
        if existing and existing.check_in_at:
            return PunchPreview("in", now, self.time.to_local(now), today, existing=existing)
        night = await self.open_night_shift(user)
        if night:
            raise ConflictError(
                f"ورود شما در {format_datetime(self.time.to_local(night.check_in_at))} هنوز خروج ندارد.\n"
                "اگر شیفت شب بوده، اول «ثبت خروج» را بزنید."
            )
        return PunchPreview("in", now, self.time.to_local(now), today)

    async def check_in(self, user: User, at_utc: datetime) -> PunchResult:
        access.require(user, Permission.ATTENDANCE_SELF)
        at_utc = self._validate_confirmed_time(at_utc)
        work_date = self.time.to_local(at_utc).date()
        record = await self.records.get_for_day(user.id, work_date, for_update=True)
        if record and record.check_in_at:
            return PunchResult(record, created=False)
        if record is None:
            record = AttendanceRecord(user_id=user.id, work_date=work_date, created_by=user.id)
            try:
                async with self.session.begin_nested():
                    self.records.add(record)
            except IntegrityError:
                # A concurrent request (double tap) created it first.
                existing = await self.records.get_for_day(user.id, work_date, for_update=True)
                if existing is None:
                    raise
                if existing.check_in_at:
                    return PunchResult(existing, created=False)
                record = existing
        if record.check_out_at and ensure_utc(record.check_out_at) <= at_utc:
            raise ConflictError("برای امروز خروج ثبت شده و ورود باید قبل از آن باشد.")
        record.check_in_at = at_utc
        record.check_in_source = RecordSource.BOT_LIVE
        record.updated_by = user.id
        self.audit.record(
            AuditAction.ATTENDANCE_CHECK_IN, "attendance_record", record.id, user.id, {"at": at_utc.isoformat()}
        )
        await self.session.flush()
        calc = await self._refresh_summary(user, record)
        await self.session.commit()
        return PunchResult(record, created=True, calculation=calc)

    # --- live check-out ----------------------------------------------------------

    async def preview_check_out(self, user: User) -> PunchPreview:
        access.require(user, Permission.ATTENDANCE_SELF)
        now = self.time.now_utc().replace(microsecond=0)
        today = self.time.today()
        record = await self.records.get_for_day(user.id, today)
        if record and record.check_out_at:
            return PunchPreview("out", now, self.time.to_local(now), today, existing=record)
        if record is None or record.check_in_at is None:
            night = await self.open_night_shift(user)
            if night:
                return PunchPreview("out", now, self.time.to_local(now), night.work_date)
            if not self.policy.allow_checkout_without_checkin:
                raise ConflictError(
                    "برای امروز ورودی ثبت نشده است.\n"
                    "اگر ورود را فراموش کرده‌اید، از «ثبت فراموشی ورود/خروج» استفاده کنید."
                )
        return PunchPreview("out", now, self.time.to_local(now), today)

    async def check_out(self, user: User, at_utc: datetime, work_date: date) -> PunchResult:
        access.require(user, Permission.ATTENDANCE_SELF)
        at_utc = self._validate_confirmed_time(at_utc)
        if work_date not in (self.time.to_local(at_utc).date(), self.time.to_local(at_utc).date() - timedelta(days=1)):
            raise ExpiredActionError()
        record = await self.records.get_for_day(user.id, work_date, for_update=True)
        if record and record.check_out_at:
            return PunchResult(record, created=False)
        if record is None or record.check_in_at is None:
            if not self.policy.allow_checkout_without_checkin:
                raise ConflictError("ورودی برای این روز ثبت نشده است.")
            if record is None:
                record = self.records.add(AttendanceRecord(user_id=user.id, work_date=work_date, created_by=user.id))
        if record.check_in_at:
            check_in = ensure_utc(record.check_in_at)
            if at_utc <= check_in:
                raise ValidationError("زمان خروج باید بعد از زمان ورود باشد.")
            if at_utc - check_in > self.max_shift:
                raise ValidationError(
                    f"مدت حضور بیشتر از {self.policy.max_shift_hours} ساعت می‌شود. "
                    "لطفاً خروج را از «ثبت فراموشی ورود/خروج» ثبت کنید."
                )
        record.check_out_at = at_utc
        record.check_out_source = RecordSource.BOT_LIVE
        record.updated_by = user.id
        await self.session.flush()
        self.audit.record(
            AuditAction.ATTENDANCE_CHECK_OUT, "attendance_record", record.id, user.id, {"at": at_utc.isoformat()}
        )
        calc = await self._refresh_summary(user, record)
        await self.session.commit()
        return PunchResult(record, created=True, calculation=calc)

    def _validate_confirmed_time(self, at_utc: datetime) -> datetime:
        at_utc = ensure_utc(at_utc)
        now = self.time.now_utc()
        if at_utc > now + _CLOCK_SKEW or now - at_utc > timedelta(seconds=self.policy.live_confirmation_ttl_seconds):
            raise ExpiredActionError("زمان این تأیید گذشته است. لطفاً دوباره از منو اقدام کنید.")
        return at_utc

    # --- forgotten entries ---------------------------------------------------------

    def correction_date_window(self) -> tuple[date, date]:
        """The earliest and latest dates a forgotten entry may be submitted for."""
        today = self.time.today()
        earliest = today - timedelta(days=self.policy.max_manual_correction_age_days)
        latest = today if self.policy.allow_same_day_manual_correction else today - timedelta(days=1)
        if self.policy.allow_future_manual_correction:
            latest = today + timedelta(days=7)
        return earliest, latest

    async def validate_correction(self, user: User, data: CorrectionInput) -> None:
        """Run every check of submit_correction without saving, so a wrong time is rejected right away."""
        access.require(user, Permission.ATTENDANCE_SELF)
        await self._plan_correction(user, data, for_update=False)

    async def submit_correction(self, user: User, data: CorrectionInput) -> CorrectionResult:
        access.require(user, Permission.ATTENDANCE_SELF)
        plan = await self._plan_correction(user, data, for_update=True)
        record, now = plan.record, plan.now
        needs_in, needs_out = plan.needs_in, plan.needs_out
        current_in, current_out, new_in, new_out = plan.current_in, plan.current_out, plan.new_in, plan.new_out

        status = (
            CorrectionStatus.PENDING_APPROVAL
            if self.policy.manual_correction_requires_approval
            else CorrectionStatus.APPLIED
        )
        if record is None:
            record = AttendanceRecord(user_id=user.id, work_date=data.work_date, created_by=user.id)
            try:
                async with self.session.begin_nested():
                    self.records.add(record)
            except IntegrityError:
                raise ConflictError("رکورد این روز همزمان در حال تغییر است. دوباره تلاش کنید.") from None

        correction = self.records.add_correction(
            AttendanceManualCorrection(
                record_id=record.id,
                user_id=user.id,
                work_date=data.work_date,
                correction_type=data.correction_type,
                claimed_check_in_at=new_in if needs_in else None,
                claimed_check_out_at=new_out if needs_out else None,
                previous_check_in_at=current_in,
                previous_check_out_at=current_out,
                reason=data.reason,
                status=status,
                submitted_by=user.id,
                submitted_at=now,
            )
        )
        if status == CorrectionStatus.APPLIED:
            source = RecordSource.MANUAL_TODAY if data.same_day else RecordSource.MANUAL_CORRECTION
            if needs_in:
                record.check_in_at = new_in
                record.check_in_source = source
            if needs_out:
                record.check_out_at = new_out
                record.check_out_source = source
            record.updated_by = user.id
        await self.session.flush()
        self.audit.record(
            AuditAction.ATTENDANCE_MANUAL_CORRECTION,
            "attendance_record",
            record.id,
            user.id,
            {
                "correction_id": str(correction.id),
                "type": data.correction_type.value,
                "work_date": data.work_date.isoformat(),
                "claimed_in": new_in.isoformat() if needs_in and new_in else None,
                "claimed_out": new_out.isoformat() if needs_out and new_out else None,
                "previous_in": current_in.isoformat() if current_in else None,
                "previous_out": current_out.isoformat() if current_out else None,
                "status": status.value,
            },
        )
        if status == CorrectionStatus.APPLIED:
            await self._refresh_summary(user, record)
        await self.session.commit()
        return CorrectionResult(
            correction_id=correction.id,
            status=status,
            record=record,
            check_in_local=self.time.to_local(new_in) if new_in else None,
            check_out_local=self.time.to_local(new_out) if new_out else None,
        )

    async def _plan_correction(self, user: User, data: CorrectionInput, for_update: bool) -> _CorrectionPlan:
        """Validate a forgotten entry and work out the new check-in/out times (UTC)."""
        earliest, latest = self.correction_date_window()
        if data.same_day:
            # Today's manual time (yesterday too, for the check-out of a night shift)
            latest = self.time.today()
            earliest = latest - timedelta(days=1)
        if data.work_date > latest:
            raise ValidationError("این تاریخ هنوز نرسیده یا ثبت اصلاحیه برای آن مجاز نیست.")
        if data.work_date < earliest:
            raise ValidationError(
                f"ثبت فراموشی فقط تا {self.policy.max_manual_correction_age_days} روز گذشته امکان‌پذیر است."
            )
        needs_in = data.correction_type in (CorrectionType.CHECK_IN, CorrectionType.BOTH)
        needs_out = data.correction_type in (CorrectionType.CHECK_OUT, CorrectionType.BOTH)
        if (needs_in and data.check_in_time is None) or (needs_out and data.check_out_time is None):
            raise ValidationError("ساعت ورود یا خروج وارد نشده است.")

        record = await self.records.get_for_day(user.id, data.work_date, for_update=for_update)
        now = self.time.now_utc()
        current_in = ensure_utc(record.check_in_at) if record else None
        current_out = ensure_utc(record.check_out_at) if record else None

        new_in = self.time.local_to_utc(data.work_date, data.check_in_time) if needs_in else current_in
        new_out = None
        if needs_out:
            new_out = self.time.local_to_utc(data.work_date, data.check_out_time)
            # A check-out clock time earlier than the check-in means the shift crossed midnight.
            if new_in is not None and new_out <= new_in:
                new_out = self.time.local_to_utc(data.work_date + timedelta(days=1), data.check_out_time)
        else:
            new_out = current_out

        self._validate_correction(data, now, current_in, current_out, new_in, new_out)
        await self._validate_neighbours(user.id, data.work_date, new_in, new_out)
        return _CorrectionPlan(record, now, needs_in, needs_out, current_in, current_out, new_in, new_out)

    # --- editing a registered time ---------------------------------------------------------------

    def edit_date_window(self) -> tuple[date, date]:
        today = self.time.today()
        return today - timedelta(days=self.policy.max_manual_correction_age_days), today

    async def validate_edit(self, user: User, data: EditInput) -> tuple[datetime, datetime]:
        """(previous, new) UTC time of the edited check-in/out, or a Persian error."""
        access.require(user, Permission.ATTENDANCE_SELF)
        _, previous, new = await self._plan_edit(user, data, for_update=False)
        return previous, new

    async def edit_entry(self, user: User, data: EditInput) -> CorrectionResult:
        access.require(user, Permission.ATTENDANCE_SELF)
        record, previous, new = await self._plan_edit(user, data, for_update=True)
        kind = CorrectionType.CHECK_IN if data.kind == "in" else CorrectionType.CHECK_OUT
        status = (
            CorrectionStatus.PENDING_APPROVAL
            if self.policy.manual_correction_requires_approval
            else CorrectionStatus.APPLIED
        )
        now = self.time.now_utc()
        correction = self.records.add_correction(
            AttendanceManualCorrection(
                record_id=record.id,
                user_id=user.id,
                work_date=data.work_date,
                correction_type=kind,
                claimed_check_in_at=new if data.kind == "in" else None,
                claimed_check_out_at=new if data.kind == "out" else None,
                previous_check_in_at=ensure_utc(record.check_in_at) if record.check_in_at else None,
                previous_check_out_at=ensure_utc(record.check_out_at) if record.check_out_at else None,
                reason="edit",
                status=status,
                submitted_by=user.id,
                submitted_at=now,
            )
        )
        if status == CorrectionStatus.APPLIED:
            if data.kind == "in":
                record.check_in_at, record.check_in_source = new, RecordSource.USER_EDIT
            else:
                record.check_out_at, record.check_out_source = new, RecordSource.USER_EDIT
            record.updated_by = user.id
        await self.session.flush()
        self.audit.record(
            AuditAction.ATTENDANCE_MANUAL_CORRECTION,
            "attendance_record",
            record.id,
            user.id,
            {
                "correction_id": str(correction.id),
                "edit": data.kind,
                "work_date": data.work_date.isoformat(),
                "previous": previous.isoformat(),
                "new": new.isoformat(),
                "status": status.value,
            },
        )
        if status == CorrectionStatus.APPLIED:
            await self._refresh_summary(user, record)
        await self.session.commit()
        in_at = new if data.kind == "in" else record.check_in_at
        out_at = new if data.kind == "out" else record.check_out_at
        return CorrectionResult(
            correction_id=correction.id,
            status=status,
            record=record,
            check_in_local=self.time.to_local(in_at) if in_at else None,
            check_out_local=self.time.to_local(out_at) if out_at else None,
        )

    async def _plan_edit(
        self, user: User, data: EditInput, for_update: bool
    ) -> tuple[AttendanceRecord, datetime, datetime]:
        earliest, latest = self.edit_date_window()
        if not earliest <= data.work_date <= latest:
            raise ValidationError(
                f"ویرایش فقط برای امروز و {self.policy.max_manual_correction_age_days} روز گذشته امکان‌پذیر است."
            )
        label = "ورود" if data.kind == "in" else "خروج"
        record = await self.records.get_for_day(user.id, data.work_date, for_update=for_update)
        current_in = ensure_utc(record.check_in_at) if record and record.check_in_at else None
        current_out = ensure_utc(record.check_out_at) if record and record.check_out_at else None
        previous = current_in if data.kind == "in" else current_out
        if record is None or previous is None:
            raise ConflictError(f"برای این روز {label}ی ثبت نشده که ویرایش شود. از «ثبت فراموشی» استفاده کنید.")
        new = self.time.local_to_utc(data.work_date, data.new_time)
        # A check-out clock time earlier than the check-in means the shift crossed midnight.
        if data.kind == "out" and current_in is not None and new <= current_in:
            new = self.time.local_to_utc(data.work_date + timedelta(days=1), data.new_time)
        if new == previous:
            raise ValidationError(f"همین ساعت از قبل برای {label} ثبت است.")
        if new > self.time.now_utc() + _CLOCK_SKEW and not self.policy.allow_future_manual_correction:
            raise ValidationError("زمان واردشده در آینده است.")
        new_in = new if data.kind == "in" else current_in
        new_out = new if data.kind == "out" else current_out
        if new_in and new_out:
            if new_out <= new_in:
                raise ValidationError("ساعت خروج باید بعد از ساعت ورود باشد.")
            if new_out - new_in > self.max_shift:
                raise ValidationError(f"مدت حضور نمی‌تواند بیشتر از {self.policy.max_shift_hours} ساعت باشد.")
        await self._validate_neighbours(user.id, data.work_date, new_in, new_out)
        return record, previous, new

    def _validate_correction(
        self,
        data: CorrectionInput,
        now: datetime,
        current_in: datetime | None,
        current_out: datetime | None,
        new_in: datetime | None,
        new_out: datetime | None,
    ) -> None:
        kind = data.correction_type
        in_future = (new_in and new_in > now) or (new_out and new_out > now)
        if in_future and not self.policy.allow_future_manual_correction:
            raise ValidationError("زمان واردشده در آینده است.")
        if kind == CorrectionType.BOTH and (current_in or current_out):
            raise ConflictError("برای این روز قبلاً ورود یا خروج ثبت شده است. فقط مورد جاافتاده را ثبت کنید.")
        # An existing check-in may only be moved earlier (the person pressed the button late).
        if kind == CorrectionType.CHECK_IN and current_in is not None and (new_in is None or new_in >= current_in):
            raise ConflictError(f"ورود این روز قبلاً ساعت {format_time(self.time.to_local(current_in))} ثبت شده است.")
        if kind == CorrectionType.CHECK_OUT:
            if current_out is not None:
                raise ConflictError(
                    f"خروج این روز قبلاً ساعت {format_time(self.time.to_local(current_out))} ثبت شده است."
                )
            if current_in is None and not self.policy.allow_checkout_without_checkin:
                raise ConflictError("برای این روز ورودی ثبت نشده است. اول «ورود» این روز را ثبت کنید، بعد خروج را.")
        if new_in and new_out:
            if new_out <= new_in:
                raise ValidationError("ساعت خروج باید بعد از ساعت ورود باشد.")
            if new_out - new_in > self.max_shift:
                raise ValidationError(f"مدت حضور نمی‌تواند بیشتر از {self.policy.max_shift_hours} ساعت باشد.")

    async def _validate_neighbours(
        self, user_id: UUID, work_date: date, new_in: datetime | None, new_out: datetime | None
    ) -> None:
        previous = await self.records.get_for_day(user_id, work_date - timedelta(days=1))
        if previous and previous.check_out_at and new_in and new_in < ensure_utc(previous.check_out_at):
            raise ConflictError("این ساعت ورود با خروج روز قبل هم‌پوشانی دارد.")
        following = await self.records.get_for_day(user_id, work_date + timedelta(days=1))
        if following and following.check_in_at and new_out and new_out > ensure_utc(following.check_in_at):
            raise ConflictError("این ساعت خروج بعد از ورود روز بعد است.")

    # --- calculation -----------------------------------------------------------------

    def calculate(self, user: User, work_date: date, record: AttendanceRecord | None) -> DailyCalculation:
        schedule = self.schedules.for_day(work_date, user.schedule_code, user.workday_codes)
        check_in = self.time.to_local(record.check_in_at) if record and record.check_in_at else None
        check_out = self.time.to_local(record.check_out_at) if record and record.check_out_at else None
        calculator = HOURLY_POLICY if schedule.hourly else self.calculator
        return calculator.calculate(schedule, check_in, check_out)

    async def _refresh_summary(self, user: User, record: AttendanceRecord) -> DailyCalculation:
        calc = self.calculate(user, record.work_date, record)
        summary = await self.records.get_summary(user.id, record.work_date)
        if summary is None:
            summary = self.records.add_summary(
                AttendanceDailySummary(
                    user_id=user.id, work_date=record.work_date, required_minutes=0, status=calc.status, policy_name=""
                )
            )
        summary.record_id = record.id
        summary.status = calc.status
        summary.gross_worked_minutes = calc.gross_worked_minutes
        summary.required_minutes = calc.required_minutes
        summary.late_minutes = calc.late_minutes
        summary.floating_late_minutes = calc.floating_late_minutes
        summary.early_departure_minutes = calc.early_departure_minutes
        summary.allowed_late_minutes = calc.allowed_late_minutes
        summary.allowed_early_minutes = calc.allowed_early_minutes
        summary.raw_balance_minutes = calc.raw_balance_minutes
        summary.overtime_minutes = calc.overtime_minutes
        summary.deficit_minutes = calc.deficit_minutes
        summary.net_balance_minutes = calc.net_balance_minutes
        summary.policy_name = HOURLY_POLICY.name if calc.hourly else self.calculator.name
        summary.calculated_at = self.time.now_utc()
        await self.session.flush()
        return calc

    # --- reports -----------------------------------------------------------------------

    @property
    def holidays(self) -> dict[date, str]:
        """Days off for everyone, with their names."""
        return dict(self.schedules.holidays)

    @property
    def sheet_rules(self) -> SheetRules:
        def clock(value: time) -> int:
            return value.hour * 60 + value.minute

        p = self.policy
        return SheetRules(
            floating_start=clock(p.standard_start_time),
            floating_end=clock(p.floating_check_in_until or p.standard_start_time),
            allowed_exit=clock(p.standard_end_time),
            late_cap=p.monthly_unauthorized_late_cap_minutes,
            early_cap=p.monthly_early_departure_cap_minutes,
            surplus_factor=p.final_work_surplus_factor,
            penalty_factor=p.final_work_penalty_factor,
        )

    async def employee_report(self, user: User, start: date, end: date) -> EmployeeReport:
        records = await self.records.list_between(start, end, user.id)
        corrections = await self.records.corrections_between(start, end, user.id)
        leaves = await self.leaves.list_filtered(start=start, end=end, statuses={LeaveStatus.APPROVED}, user_id=user.id)
        return self._build_report(user, start, end, records, corrections, leaves)

    async def my_week(self, user: User) -> EmployeeReport:
        access.require(user, Permission.ATTENDANCE_SELF)
        start, end = week_range(self.time.today())
        return await self.employee_report(user, start, end)

    async def my_month(self, user: User) -> EmployeeReport:
        access.require(user, Permission.ATTENDANCE_SELF)
        start, end = month_range(self.time.today())
        return await self.employee_report(user, start, end)

    async def team_reports(
        self, actor: User, start: date, end: date, user_id: UUID | None = None
    ) -> list[EmployeeReport]:
        access.require(actor, Permission.ATTENDANCE_REPORT_ALL)
        if user_id is not None:
            target = await self.users.get(user_id)
            if target is None:
                raise NotFoundError("کارمند پیدا نشد.")
            users = [target]
        else:
            users = await self.users.list_active_employees()
        records = await self.records.list_between(start, end)
        corrections = await self.records.corrections_between(start, end)
        leaves = await self.leaves.list_filtered(start=start, end=end, statuses={LeaveStatus.APPROVED})
        reports = []
        for u in users:
            mine = [r for r in records if r.user_id == u.id]
            mine_corr = [c for c in corrections if c.user_id == u.id]
            mine_leave = [lv for lv in leaves if lv.user_id == u.id]
            reports.append(self._build_report(u, start, end, mine, mine_corr, mine_leave))
        self.audit.record(
            AuditAction.REPORT_GENERATED,
            "attendance_report",
            None,
            actor.id,
            {"start": start.isoformat(), "end": end.isoformat(), "user_id": str(user_id) if user_id else None},
        )
        await self.session.commit()
        return reports

    def team_summary(self, reports: list[EmployeeReport], start: date, end: date) -> TeamSummary:
        rows = [row for rep in reports for row in rep.rows]
        return TeamSummary(
            start=start,
            end=end,
            employee_count=len(reports),
            present_count=sum(r.calculation.status == DayStatus.COMPLETE for r in rows),
            absent_count=sum(r.calculation.status == DayStatus.ABSENT for r in rows),
            missing_checkout_count=sum(r.calculation.status == DayStatus.MISSING_CHECK_OUT for r in rows),
            late_count=sum(r.calculation.late_minutes > 0 for r in rows),
            manual_count=sum(r.is_manual for r in rows),
        )

    def _build_report(
        self,
        user: User,
        start: date,
        end: date,
        records: list[AttendanceRecord],
        corrections: list[AttendanceManualCorrection],
        leaves: Iterable[LeaveRequest] = (),
    ) -> EmployeeReport:
        today = self.time.today()
        by_date = {r.work_date: r for r in records}
        leave_by_day = self._leave_by_day(leaves, start, end, user)
        notes: dict[date, list[str]] = {}
        for c in corrections:
            submitted = format_datetime(self.time.to_local(c.submitted_at))
            notes.setdefault(c.work_date, []).append(f"{c.correction_type.value} ({c.status.value}) {submitted}")
        rows: list[DayRow] = []
        for day in iter_days(start, end):
            record = by_date.get(day)
            is_workday = self.schedules.is_workday(day, user.workday_codes)
            # Future days and non-workdays without a record are not part of the report, and
            # hourly staff only have the days they came in.
            if record is None and (day > today or not is_workday or user.is_hourly):
                continue
            if record is None and day == today:
                continue
            calc = self.calculate(user, day, record)
            rows.append(
                DayRow(
                    user_id=user.id,
                    work_date=day,
                    check_in_local=calc.check_in,
                    check_out_local=calc.check_out,
                    check_in_source=record.check_in_source if record else None,
                    check_out_source=record.check_out_source if record else None,
                    calculation=calc,
                    is_workday=is_workday,
                    correction_notes=notes.get(day, []),
                    is_today=day == today,
                    paid_leave_minutes=leave_by_day.get(day, (0, None))[0],
                    leave_note=leave_by_day.get(day, (0, None))[1],
                )
            )
        sheet = None if user.is_hourly else sheet_month((r.sheet_day for r in rows), self.sheet_rules)
        report = EmployeeReport(
            user.id,
            user.full_name,
            user.messenger_user_id,
            start,
            end,
            rows,
            Totals.of(rows),
            hourly=user.is_hourly,
            sheet=sheet,
            sheet_rules=None if user.is_hourly else self.sheet_rules,
        )
        # Monthly caps only make sense for full-time staff and a report covering exactly one Jalali month.
        if not user.is_hourly and (start, end) == month_range(start):
            report = replace(
                report,
                late_cap_minutes=self.policy.monthly_unauthorized_late_cap_minutes,
                early_cap_minutes=self.policy.monthly_early_departure_cap_minutes,
            )
        return report

    def _leave_by_day(
        self, leaves: Iterable[LeaveRequest], start: date, end: date, user: User
    ) -> dict[date, tuple[int, str]]:
        """Approved leave per day: (paid minutes for column I of the timesheet, note)."""
        result: dict[date, tuple[int, str]] = {}
        for lv in leaves:
            paid = lv.pay_type == LeavePayType.PAID
            kind = "با حقوق" if paid else "بدون حقوق"
            if lv.unit == LeaveUnit.HOURLY:
                minutes = lv.duration_minutes or 0
                label = f"مرخصی ساعتی {kind} {format_minutes(minutes)}"
                days = [lv.start_date] if start <= lv.start_date <= end else []
            else:
                minutes = self.policy.standard_workday_minutes
                label = f"مرخصی روزانه {kind}"
                days = [
                    d
                    for d in iter_days(max(start, lv.start_date), min(end, lv.end_date))
                    if self.leave_policy.count_non_workdays_in_daily_leave
                    or self.schedules.is_workday(d, user.workday_codes)
                ]
            for d in days:
                old_minutes, old_note = result.get(d, (0, ""))
                note = f"{old_note} | {label}" if old_note else label
                result[d] = (old_minutes + (minutes if paid else 0), note)
        return result

    # --- reminders ---------------------------------------------------------------------

    def is_workday(self, day: date, user: User | None = None) -> bool:
        return self.schedules.is_workday(day, user.workday_codes if user else None)

    def is_holiday(self, day: date) -> bool:
        return day in self.schedules.holidays

    async def _on_daily_leave(self, day: date) -> set[UUID]:
        leaves = await self.leaves.list_filtered(start=day, end=day, statuses={LeaveStatus.APPROVED})
        return {lv.user_id for lv in leaves if lv.unit == LeaveUnit.DAILY}

    async def check_in_reminder_recipients(self, day: date) -> list[User]:
        employees = await self.users.list_active_employees()
        checked_in = await self.records.user_ids_checked_in_on(day)
        on_leave = await self._on_daily_leave(day)
        return [
            u
            for u in employees
            if u.id not in checked_in and u.id not in on_leave and self.is_workday(day, u)  # own workdays
        ]

    async def check_out_reminder_recipients(self, day: date) -> list[tuple[User, AttendanceRecord]]:
        open_records = await self.records.open_records_on(day)
        users = await self.users.get_many({r.user_id for r in open_records})
        return [(users[r.user_id], r) for r in open_records if r.user_id in users and users[r.user_id].is_active]

    def reminder_due(self, at: time) -> bool:
        """True from the configured time until the catch-up window ends (survives restarts)."""
        now = self.time.now_local()
        scheduled = datetime.combine(now.date(), at, tzinfo=now.tzinfo)
        return scheduled <= now < scheduled + timedelta(minutes=self.policy.reminder_catchup_minutes)

    def describe_day(self, day: date) -> str:
        return format_date(day)
