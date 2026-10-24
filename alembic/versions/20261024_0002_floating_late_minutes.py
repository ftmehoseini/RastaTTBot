"""attendance summary: floating (allowed) late minutes

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-24 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "attendance_daily_summaries",
        sa.Column("floating_late_minutes", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("attendance_daily_summaries", "floating_late_minutes")
