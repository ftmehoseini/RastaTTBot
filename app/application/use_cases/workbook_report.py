"""Write a person's check-in/out into their Excel workbook, let Excel calculate, send the result.

    database record → write «ساعت ورود»/«ساعت خروج» into a new version of the file (formulas untouched)
                    → LibreOffice recalculates a copy → read the day's row and the month's totals
                    → check for formula errors and that the values read back are the ones written
                    → Telegram message to the employee

Nothing is calculated here. If anything fails, no figures are sent: the employee is told the report is
delayed and the super admins get the reason. Run one at a time per person (the Celery task holds a
Redis lock), so two check-ins never write the same file at once.
"""

import asyncio
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from app.application.presenters import workbook as texts
from app.application.services.container import Services
from app.application.services.notification_service import SendOutcome
from app.core.logging import get_logger
from app.core.security.rbac import RoleCode
from app.core.utils.jalali import iter_days, month_range, to_jalali
from app.domain.attendance.models import AttendanceRecord
from app.domain.notifications.models import NotificationType
from app.infrastructure.excel.recalc import RecalculationError, recalculate
from app.infrastructure.excel.workbook import (
    DayTimes,
    PeriodReport,
    WorkbookError,
    WorkbookReport,
    read_period,
    read_report,
    write_times,
)
from app.infrastructure.repositories.attendance import AttendanceRepository
from app.infrastructure.repositories.users import UserRepository

log = get_logger(__name__)


@dataclass(frozen=True)
class WorkbookRun:
    status: str  # no_workbook | written | sent | not_delivered | failed
    report: WorkbookReport | None = None
    error: str | None = None


def _minutes(services: Services, work_date: date, value: datetime | None) -> int | None:
    if value is None:
        return None
    local = services.time.to_local(value)
    return (local.date() - work_date).days * 1440 + local.hour * 60 + local.minute


def _day_times(services: Services, record: AttendanceRecord) -> DayTimes:
    return DayTimes(
        day=to_jalali(record.work_date).day,
        check_in=_minutes(services, record.work_date, record.check_in_at),
        check_out=_minutes(services, record.work_date, record.check_out_at),
    )


async def update_workbook(
    services: Services, user_id: UUID, work_date: date, *, sync_month: bool = False, send_report: bool = True
) -> WorkbookRun:
    """`sync_month` writes every record of the work date's month (after a file is assigned or replaced)."""
    users = UserRepository(services.session)
    user = await users.get(user_id)
    workbook = await services.workbooks.repo.for_user(user_id, for_update=True)
    if user is None or workbook is None:
        return WorkbookRun("no_workbook")
    records_repo = AttendanceRepository(services.session)
    if sync_month:
        start, end = month_range(work_date)
        records = await records_repo.list_between(start, end, user_id)
    else:
        record = await records_repo.get_for_day(user_id, work_date)
        records = [record] if record else []
    month = to_jalali(work_date).month
    # Read before anything can fail: the error messages below need them.
    full_name, title = user.full_name, workbook.title
    days = [_day_times(services, r) for r in records]
    today = next((d for d in days if d.day == to_jalali(work_date).day), None)

    try:
        content = await asyncio.to_thread(services.workbook_store.read, workbook.id, workbook.version)
        if days:
            content = await asyncio.to_thread(write_times, content, month, days)
            await asyncio.to_thread(services.workbook_store.save, workbook.id, workbook.version + 1, content)
            workbook.version += 1
            await services.session.commit()
        if not send_report:
            return WorkbookRun("written")
        recalculated = await asyncio.to_thread(recalculate, content)
        report = await asyncio.to_thread(read_report, recalculated, month, to_jalali(work_date).day, today)
        if not report.ok:
            raise WorkbookError("خطای فرمول در فایل: " + "، ".join(report.errors[:5]))
    except (WorkbookError, RecalculationError, OSError) as exc:
        reason = str(exc) if isinstance(exc, WorkbookError) else "محاسبه‌ی فایل با LibreOffice انجام نشد."
        log.warning("workbook update failed", user_id=str(user_id), work_date=work_date.isoformat(), error=str(exc))
        if send_report:
            await services.notifications.notify_user(
                user, texts.report_failed_for_employee(work_date), NotificationType.GENERIC
            )
        for admin in await users.list_active_with_role(RoleCode.SUPER_ADMIN):
            await services.notifications.notify_user(
                admin,
                texts.report_failed_for_admin(full_name, work_date, title, reason),
                NotificationType.GENERIC,
            )
        return WorkbookRun("failed", error=reason)

    outcome = await services.notifications.notify_user(
        user, texts.report_text(full_name, work_date, report), NotificationType.GENERIC
    )
    return WorkbookRun("sent" if outcome == SendOutcome.SENT else "not_delivered", report=report)


@dataclass(frozen=True)
class PeriodRun:
    report: PeriodReport
    dates: list[date]  # the days asked for, in order (a day whose month sheet is missing has no row)


async def workbook_period(
    services: Services, user_id: UUID, start: date, end: date, with_days: bool
) -> PeriodRun | None:
    """The person's days from `start` to `end` (not after today) and the current month's totals, all
    read from their Excel file after LibreOffice recalculates a copy. None when they have no file.

    Raises WorkbookError or RecalculationError; the caller tells the person and the admins.
    """
    workbook = await services.workbooks.repo.for_user(user_id)
    if workbook is None:
        return None
    today = services.time.today()
    dates = iter_days(start, min(end, today)) if with_days else []
    source = await asyncio.to_thread(services.workbook_store.read, workbook.id, workbook.version)
    recalculated = await asyncio.to_thread(recalculate, source)
    wanted = [(to_jalali(d).month, to_jalali(d).day) for d in dates]
    report = await asyncio.to_thread(read_period, source, recalculated, wanted, to_jalali(min(end, today)).month)
    if not report.ok:
        raise WorkbookError("خطای فرمول در فایل: " + "، ".join(report.errors[:5]))
    return PeriodRun(report, dates)


async def notify_admins_of_failure(services: Services, full_name: str, title: str, reason: str) -> None:
    for admin in await UserRepository(services.session).list_active_with_role(RoleCode.SUPER_ADMIN):
        await services.notifications.notify_user(
            admin,
            texts.report_failed_for_admin(full_name, services.time.today(), title, reason),
            NotificationType.GENERIC,
        )
