"""attendance_workbooks: admin-uploaded Excel files that calculate each person's attendance

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-09 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "attendance_workbooks",
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("sheets", sa.String(length=300), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("uploaded_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_attendance_workbooks_user_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by"], ["users.id"], name=op.f("fk_attendance_workbooks_uploaded_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_attendance_workbooks")),
        sa.UniqueConstraint("user_id", name=op.f("uq_attendance_workbooks_user_id")),
    )


def downgrade() -> None:
    op.drop_table("attendance_workbooks")
