"""employee_roster: staff numbers allowed to register

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-27 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "employee_roster",
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("full_name", sa.String(length=120), nullable=True),
        sa.Column("hourly", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_employee_roster_user_id_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_employee_roster")),
        sa.UniqueConstraint("user_id", name=op.f("uq_employee_roster_user_id")),
    )
    op.create_index(op.f("ix_employee_roster_code"), "employee_roster", ["code"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_employee_roster_code"), table_name="employee_roster")
    op.drop_table("employee_roster")
