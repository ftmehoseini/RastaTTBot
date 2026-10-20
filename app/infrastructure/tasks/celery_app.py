"""Celery application and Beat schedule.

Reminder tasks run every 10 minutes. Each task decides for itself whether its reminder is due
(configured time + catch-up window) and claims each recipient in notification_logs, so a restart,
a missed Beat tick or several workers never cause a missing or duplicate reminder.
"""

from celery import Celery
from celery.schedules import crontab

from app.core.config import get_policy, get_settings

settings = get_settings()

celery_app = Celery(
    "rasta_bot",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.infrastructure.tasks.jobs"],
)

celery_app.conf.update(
    timezone=get_policy().organization.timezone,
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_time_limit=900,
    task_soft_time_limit=840,
    result_expires=3600,
    broker_connection_retry_on_startup=True,
    beat_schedule={
        "attendance-check-in-reminders": {
            "task": "attendance.check_in_reminders",
            "schedule": crontab(minute="*/10"),
        },
        "attendance-check-out-reminders": {
            "task": "attendance.check_out_reminders",
            "schedule": crontab(minute="*/10"),
        },
        "maintenance-cleanup-files": {
            "task": "maintenance.cleanup_files",
            "schedule": crontab(minute=17),
        },
    },
)
