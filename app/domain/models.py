"""Imports every ORM model so `Base.metadata` is complete (used by Alembic and tests)."""

from app.domain.approvals.models import ApprovalStep, WorkflowAction
from app.domain.attendance.models import AttendanceDailySummary, AttendanceManualCorrection, AttendanceRecord
from app.domain.audit.models import AuditLog
from app.domain.food.models import (
    FoodGuestOrder,
    FoodMenu,
    FoodMenuItem,
    FoodOrder,
    FoodOrderItem,
    FoodWeeklySummary,
)
from app.domain.leave.models import LeaveBalance, LeaveRequest
from app.domain.notifications.models import NotificationLog
from app.domain.users.models import Role, User, UserRole

__all__ = [
    "ApprovalStep",
    "AttendanceDailySummary",
    "AttendanceManualCorrection",
    "AttendanceRecord",
    "AuditLog",
    "FoodGuestOrder",
    "FoodMenu",
    "FoodMenuItem",
    "FoodOrder",
    "FoodOrderItem",
    "FoodWeeklySummary",
    "LeaveBalance",
    "LeaveRequest",
    "NotificationLog",
    "Role",
    "User",
    "UserRole",
    "WorkflowAction",
]
