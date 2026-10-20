"""Centralized role-based access control.

Roles are stored per user in the database (`user_roles`). This module only maps roles to
permissions, so every permission check in the codebase goes through `has_permission`.
"""

from collections.abc import Iterable
from enum import StrEnum


class RoleCode(StrEnum):
    EMPLOYEE = "EMPLOYEE"
    ADMIN_1_SECRETARY = "ADMIN_1_SECRETARY"
    ADMIN_2_MANAGER = "ADMIN_2_MANAGER"
    SUPER_ADMIN = "SUPER_ADMIN"


ROLE_TITLES_FA: dict[RoleCode, str] = {
    RoleCode.EMPLOYEE: "کارمند",
    RoleCode.ADMIN_1_SECRETARY: "ادمین ۱ (منشی)",
    RoleCode.ADMIN_2_MANAGER: "ادمین ۲ (مدیر)",
    RoleCode.SUPER_ADMIN: "مدیر کل سیستم",
}


class Permission(StrEnum):
    ATTENDANCE_SELF = "attendance.self"
    ATTENDANCE_REPORT_ALL = "attendance.report_all"
    LEAVE_SELF = "leave.self"
    LEAVE_APPROVE_STEP_1 = "leave.approve_step_1"
    LEAVE_APPROVE_STEP_2 = "leave.approve_step_2"
    LEAVE_REPORT_ALL = "leave.report_all"
    FOOD_ORDER = "food.order"
    FOOD_MENU_MANAGE = "food.menu_manage"
    FOOD_BROADCAST = "food.broadcast"
    FOOD_REPORT_ALL = "food.report_all"
    USERS_VIEW = "users.view"
    USERS_MANAGE_ROLES = "users.manage_roles"
    SYSTEM_HEALTH = "system.health"


_EMPLOYEE = {Permission.ATTENDANCE_SELF, Permission.LEAVE_SELF, Permission.FOOD_ORDER}

ROLE_PERMISSIONS: dict[RoleCode, frozenset[Permission]] = {
    RoleCode.EMPLOYEE: frozenset(_EMPLOYEE),
    RoleCode.ADMIN_1_SECRETARY: frozenset(
        {
            Permission.ATTENDANCE_REPORT_ALL,
            Permission.LEAVE_APPROVE_STEP_1,
            Permission.LEAVE_REPORT_ALL,
            Permission.FOOD_MENU_MANAGE,
            Permission.FOOD_BROADCAST,
            Permission.FOOD_REPORT_ALL,
            Permission.USERS_VIEW,
        }
    ),
    RoleCode.ADMIN_2_MANAGER: frozenset(
        {
            Permission.ATTENDANCE_REPORT_ALL,
            Permission.LEAVE_APPROVE_STEP_2,
            Permission.LEAVE_REPORT_ALL,
            Permission.FOOD_REPORT_ALL,
            Permission.USERS_VIEW,
        }
    ),
    RoleCode.SUPER_ADMIN: frozenset(Permission),
}

ADMIN_ROLES = frozenset({RoleCode.ADMIN_1_SECRETARY, RoleCode.ADMIN_2_MANAGER, RoleCode.SUPER_ADMIN})


def permissions_for(roles: Iterable[RoleCode | str]) -> frozenset[Permission]:
    granted: set[Permission] = set()
    for role in roles:
        granted |= ROLE_PERMISSIONS.get(RoleCode(role), frozenset())
    return frozenset(granted)


def has_permission(roles: Iterable[RoleCode | str], permission: Permission) -> bool:
    return permission in permissions_for(roles)


def is_admin(roles: Iterable[RoleCode | str]) -> bool:
    return any(RoleCode(r) in ADMIN_ROLES for r in roles)
