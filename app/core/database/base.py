"""Declarative base, shared column mixins and type helpers."""

import uuid
from datetime import datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

JSONType = sa.JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)


def enum_column(enum_cls: type[StrEnum], **kwargs) -> sa.Enum:
    """Enums are stored as their string value in a VARCHAR, so adding a value needs no DDL."""
    return sa.Enum(
        enum_cls,
        native_enum=False,
        length=40,
        values_callable=lambda members: [m.value for m in members],
        validate_strings=True,
        **kwargs,
    )


def utc_timestamp() -> sa.DateTime:
    return sa.DateTime(timezone=True)


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(utc_timestamp(), server_default=sa.func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        utc_timestamp(), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False
    )


class AuthorshipMixin:
    """Who created/last changed a row (user IDs; null for system jobs)."""

    created_by: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, sa.ForeignKey("users.id"), nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, sa.ForeignKey("users.id"), nullable=True)
