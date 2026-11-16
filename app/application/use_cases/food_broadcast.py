"""Tell every active employee that the weekly menu is ready. Resumable and idempotent per menu."""

from datetime import date
from uuid import UUID

from app.application.dto.messaging import InlineButtons
from app.application.presenters import food as texts
from app.application.services.container import Services
from app.application.services.notification_service import BulkResult, SendOutcome
from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.core.security.rbac import RoleCode
from app.core.utils.jalali import format_date_numeric, format_date_short
from app.core.utils.text import to_persian_digits
from app.domain.notifications.models import NotificationType
from app.domain.users.models import User
from app.infrastructure.repositories.users import UserRepository

log = get_logger(__name__)

# The button under the notification. The food router handles exactly this string (imported from here);
# notifications already in people's chats carry it too, so it must not change.
OPEN_FOOD_MENU = "food:menu"


async def broadcast_menu(services: Services, actor_id: UUID, menu_id: UUID) -> BulkResult:
    actor = await services.users.get(actor_id)
    menu = await services.food.repo.get(menu_id)
    if menu is None:
        raise NotFoundError("منو پیدا نشد.")
    recipients = await services.food.broadcast_recipients(actor)
    week = format_date_numeric(menu.week_start_date)
    # The dedup prefix includes the menu id: re-running resumes; a new menu version notifies again.
    result = await services.notifications.notify_many(
        recipients,
        texts.broadcast_message(week),
        NotificationType.FOOD_MENU_BROADCAST,
        dedup_prefix=f"menu_broadcast:{menu.id}",
        batch_key=f"menu_broadcast:{menu.id}",
        buttons=InlineButtons.of([("🍽 ثبت سفارش غذا", OPEN_FOOD_MENU)]),
    )
    await services.food.mark_notified(
        menu.id, actor, {"sent": result.sent, "failed": result.failed, "skipped": result.skipped}
    )
    return result


# --- «📣 اطلاع‌رسانی»: a sample or typed text to everyone active -------------------------------------


def announcement_message(text: str) -> str:
    return f"📣 {text.strip()}"


async def announce(
    services: Services, actor_id: UUID, announcement_id: UUID, text: str, menu_id: UUID | None
) -> BulkResult:
    """Send one announcement. Resumable: re-running the same announcement id skips who already got it."""
    actor = await services.users.get(actor_id)
    if actor is None:
        raise NotFoundError("فرستنده پیدا نشد.")
    recipients = await services.food.broadcast_recipients(actor)
    result = await services.notifications.notify_many(
        recipients,
        announcement_message(text),
        NotificationType.FOOD_MENU_BROADCAST,
        dedup_prefix=f"food_announce:{announcement_id}",
        batch_key=f"food_announce:{announcement_id}",
        buttons=InlineButtons.of([("🍽 ثبت سفارش غذا", OPEN_FOOD_MENU)]),
    )
    await services.food.record_announcement(
        actor,
        announcement_id,
        menu_id,
        text,
        {"sent": result.sent, "failed": result.failed, "skipped": result.skipped},
    )
    return result


# --- a new menu version dropped a dish someone had ordered --------------------------------------


async def notify_lost_dishes(services: Services, menu_id: UUID, lost: dict[UUID, list[tuple[date, str, int]]]) -> int:
    """Tell each person whose ordered dish is gone from the new menu. Returns how many were told."""
    users = await UserRepository(services.session).get_many(set(lost))
    told = 0
    for user_id, entries in lost.items():
        user = users.get(user_id)
        if user is None or not user.is_active:
            continue
        lines = ["⚠️ منوی غذا تغییر کرد و غذایی که سفارش داده بودید در منوی جدید نیست:"]
        for day, name, guests in entries:
            who = f"مهمان × {to_persian_digits(guests)}" if guests else "سفارش شما"
            lines.append(f"• {format_date_short(day)}: {name} ({who})")
        lines.append("لطفاً دوباره انتخاب کنید.")
        outcome = await services.notifications.notify_user(
            user,
            "\n".join(lines),
            NotificationType.GENERIC,
            dedup_key=f"menu_lost:{menu_id}:{user_id}",
            buttons=InlineButtons.of([("🍽 ثبت سفارش غذا", OPEN_FOOD_MENU)]),
        )
        if outcome == SendOutcome.SENT:
            told += 1
    return told


# --- someone changed an order after the menu was announced → tell the food admins ------------------


async def notify_food_admins(services: Services, user: User, days: list[date], lines: list[str]) -> None:
    """Best effort: a failure here never undoes or blocks the person's order."""
    if not lines or not services.policy.food.notify_food_admins_of_changes:
        return
    try:
        if not await services.food.announced(days):
            return
        admins = await UserRepository(services.session).list_active_with_role(RoleCode.FOOD_ADMIN)
        text = "\n".join([f"🍽 {user.full_name} سفارش غذا را تغییر داد:", *lines])
        for admin in admins:
            if admin.id != user.id:
                await services.notifications.notify_user(admin, text, NotificationType.GENERIC)
    except Exception:
        log.warning("could not tell the food admins about an order change", exc_info=True)
