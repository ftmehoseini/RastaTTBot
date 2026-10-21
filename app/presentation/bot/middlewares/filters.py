from aiogram.filters import Filter
from aiogram.types import TelegramObject

from app.application.services import access
from app.core.security.rbac import Permission
from app.domain.users.models import User


class IsRegistered(Filter):
    """Passes for registered, active users. Routers for business modules apply it to every handler."""

    async def __call__(self, event: TelegramObject, user: User | None = None) -> bool:
        return bool(user and user.is_active)


class HasPermission(Filter):
    def __init__(self, permission: Permission):
        self.permission = permission

    async def __call__(self, event: TelegramObject, user: User | None = None) -> bool:
        return access.can(user, self.permission)
