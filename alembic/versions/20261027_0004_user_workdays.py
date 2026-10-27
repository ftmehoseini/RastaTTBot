"""users.workdays: a person's own workdays

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-27 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("workdays", sa.String(length=40), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "workdays")
