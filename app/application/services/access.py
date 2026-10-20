"""The single entry point for permission checks."""

from app.core.exceptions import PermissionDeniedError
from app.core.logging import get_logger
from app.core.security.rbac import Permission, has_permission
from app.domain.users.models import User

log = get_logger(__name__)


def can(user: User | None, permission: Permission) -> bool:
    return bool(user and user.is_active and has_permission(user.roles, permission))


def require(user: User | None, permission: Permission) -> User:
    """Return the user if allowed; otherwise log the denial and raise PermissionDeniedError."""
    if not can(user, permission):
        log.warning(
            "permission denied",
            permission=permission.value,
            user_id=str(user.id) if user else None,
        )
        raise PermissionDeniedError(log_message=f"missing {permission}")
    assert user is not None
    return user
