"""Imports every ORM model so `Base.metadata` is complete (used by Alembic and tests)."""

from app.domain.attendance.models import (
    AttendanceDailySummary,
    AttendanceManualCorrection,
    AttendanceRecord,
    AttendanceWorkbook,
)
from app.domain.audit.models import AuditLog
from app.domain.food.models import (
    FoodGuestOrder,
    FoodMenu,
    FoodMenuItem,
    FoodOrder,
    FoodOrderItem,
    FoodWeeklySummary,
)
from app.domain.notifications.models import NotificationLog
from app.domain.users.models import Role, User, UserRole

__all__ = [
    "AttendanceDailySummary",
    "AttendanceManualCorrection",
    "AttendanceRecord",
    "AttendanceWorkbook",
    "AuditLog",
    "FoodGuestOrder",
    "FoodMenu",
    "FoodMenuItem",
    "FoodOrder",
    "FoodOrderItem",
    "FoodWeeklySummary",
    "NotificationLog",
    "Role",
    "User",
    "UserRole",
]
