"""Queue background jobs from the bot process. Tests replace these functions."""

from datetime import date
from uuid import UUID

WORKBOOK_TASK = "attendance.workbook_update"


def enqueue_workbook_update(user_id: UUID, work_date: date, sync_month: bool = False) -> None:
    """Ask the worker to write this day (or the whole month) into the person's Excel workbook."""
    from app.infrastructure.tasks.celery_app import celery_app

    celery_app.send_task(WORKBOOK_TASK, args=[str(user_id), work_date.isoformat(), sync_month])
