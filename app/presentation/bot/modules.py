"""Main-menu registry. A new module adds one MenuEntry here and its router in `dispatcher.py`."""

from collections.abc import Callable
from dataclasses import dataclass

from aiogram.types import ReplyKeyboardMarkup

from app.application.services import access
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


def _perm(permission: Permission) -> Callable[[User], bool]:
    return lambda user: access.can(user, permission)


MENU_ENTRIES: list[MenuEntry] = [
    MenuEntry(BTN_ATTENDANCE, 10, _perm(Permission.ATTENDANCE_SELF)),
    MenuEntry(BTN_LEAVE, 20, _perm(Permission.LEAVE_SELF)),
    MenuEntry(BTN_FOOD, 30, _perm(Permission.FOOD_ORDER)),
    MenuEntry(BTN_ADMIN, 90, lambda user: user.is_active and is_admin(user.roles)),
]


def main_menu(user: User) -> ReplyKeyboardMarkup:
    labels = [e.label for e in sorted(MENU_ENTRIES, key=lambda e: e.order) if e.visible(user)]
    rows = [labels[i : i + 2] for i in range(0, len(labels), 2)]
    rows.append([BTN_HELP])
    return reply_menu(rows)


HELP_TEXT = (
    "راهنمای ربات\n\n"
    f"{BTN_ATTENDANCE}: ثبت ورود و خروج، ثبت فراموشی، وضعیت امروز و گزارش هفتگی\n"
    f"{BTN_LEAVE}: درخواست مرخصی ساعتی یا روزانه و پیگیری وضعیت آن\n"
    f"{BTN_FOOD}: دیدن منوی هفته، سفارش غذا و مهمان، صورت‌حساب\n\n"
    "دستورها:\n"
    "/start شروع یا نمایش منوی اصلی\n"
    "/myid نمایش شناسه‌ی شما\n"
    "/cancel لغو عملیات جاری"
)
