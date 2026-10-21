from typing import Any
from uuid import UUID

from app.core.logging import correlation_id_var
from app.domain.audit.models import AuditAction, AuditLog
from app.infrastructure.repositories.base import Repository


class AuditRepository(Repository[AuditLog]):
    model = AuditLog

    def record(
        self,
        action: AuditAction,
        entity_type: str,
        entity_id: UUID | str | None,
        actor_user_id: UUID | None,
        details: dict[str, Any] | None = None,
    ) -> AuditLog:
        return self.add(
            AuditLog(
                action=action,
                entity_type=entity_type,
                entity_id=str(entity_id) if entity_id is not None else None,
                actor_user_id=actor_user_id,
                details=details or {},
                correlation_id=correlation_id_var.get(),
            )
        )
