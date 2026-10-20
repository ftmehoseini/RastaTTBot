"""Tell every active employee that the weekly menu is ready. Resumable and idempotent per menu."""

from uuid import UUID

from app.application.dto.messaging import InlineButtons
from app.application.presenters import food as texts
from app.application.services.container import Services
from app.application.services.notification_service import BulkResult
from app.core.exceptions import NotFoundError
from app.core.utils.jalali import format_date_numeric
from app.domain.notifications.models import NotificationType

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
