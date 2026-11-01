"""Main-menu registry. A new module adds one MenuEntry here and its router in `dispatcher.py`."""

from collections.abc import Callable
from dataclasses import dataclass

from aiogram.types import ReplyKeyboardMarkup

from app.application.services import access
from app.core.config import get_settings
from app.core.security.rbac import Permission, is_admin
from app.domain.users.models import User
from app.presentation.bot.keyboards.common import BTN_HELP, reply_menu

BTN_ATTENDANCE = "⏱ ورود و خروج"
BTN_LEAVE = "📝 مرخصی"
BTN_FOOD = "🍽 سفارش غذا"
BTN_ADMIN = "🛠 پنل مدیریت"


@dataclass(frozen=True)
class MenuEntry:
    label: str
    order: int
    visible: Callable[[User], bool]
    module: str | None = None  # BOT_MODULES name; None = always available


def module_enabled(module: str | None) -> bool:
    return module is None or module in get_settings().enabled_modules


def _perm(permission: Permission) -> Callable[[User], bool]:
    return lambda user: access.can(user, permission)


MENU_ENTRIES: list[MenuEntry] = [
    MenuEntry(BTN_ATTENDANCE, 10, _perm(Permission.ATTENDANCE_SELF), "attendance"),
    MenuEntry(BTN_LEAVE, 20, _perm(Permission.LEAVE_SELF), "leave"),
    MenuEntry(BTN_FOOD, 30, _perm(Permission.FOOD_ORDER), "food"),
    MenuEntry(BTN_ADMIN, 90, lambda user: user.is_active and is_admin(user.roles)),
]


def main_menu(user: User) -> ReplyKeyboardMarkup:
    entries = sorted(MENU_ENTRIES, key=lambda e: e.order)
    labels = [e.label for e in entries if module_enabled(e.module) and e.visible(user)]
    # Soroush draws rows left to right, so each pair is reversed: «ورود و خروج» on the right, «مرخصی» left.
    rows = [labels[i : i + 2][::-1] for i in range(0, len(labels), 2)]
    rows.append([BTN_HELP])
    return reply_menu(rows)


def help_text() -> str:
    lines = ["راهنمای ربات", ""]
    lines.append(
        f"{BTN_ATTENDANCE}: ثبت ورود و خروج امروز (اتوماتیک یا دستی)، ویرایش، ثبت فراموشی، گزارش هفتگی و ماهانه"
    )
    if module_enabled("leave"):
        lines.append(f"{BTN_LEAVE}: درخواست مرخصی ساعتی یا روزانه و پیگیری وضعیت آن")
    if module_enabled("food"):
        lines.append(f"{BTN_FOOD}: دیدن منوی هفته، سفارش غذا و مهمان، صورت‌حساب")
    lines += ["", "دستورها:", "/start شروع یا نمایش منوی اصلی", "/myid نمایش شناسه‌ی شما", "/cancel لغو عملیات جاری"]
    return "\n".join(lines)
