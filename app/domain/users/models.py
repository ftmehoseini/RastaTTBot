import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_column, utc_timestamp
from app.core.security.rbac import RoleCode

# User.schedule_code of hourly staff: only check-in, check-out and worked time are tracked for them.
SCHEDULE_HOURLY = "hourly"


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    # The messenger's numeric user ID (Telegram/Bale/Soroush). Private chat ID equals it.
    messenger_user_id: Mapped[int] = mapped_column(sa.BigInteger, unique=True, index=True)
    chat_id: Mapped[int] = mapped_column(sa.BigInteger)
    full_name: Mapped[str] = mapped_column(sa.String(120))
    username: Mapped[str | None] = mapped_column(sa.String(64))
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True, server_default=sa.true())
    registered_at: Mapped[datetime | None] = mapped_column(utc_timestamp())
    # Optional per-user work schedule name; null means the organization default.
    schedule_code: Mapped[str | None] = mapped_column(sa.String(40))
    # Own workdays as codes ("sat,sun,mon,tue,wed,thu"); null means the organization's workdays
    workdays: Mapped[str | None] = mapped_column(sa.String(40))

    role_links: Mapped[list["UserRole"]] = relationship(
        back_populates="user",
        lazy="selectin",
        cascade="all, delete-orphan",
        foreign_keys="UserRole.user_id",
    )

    @property
    def is_hourly(self) -> bool:
        return self.schedule_code == SCHEDULE_HOURLY

    @property
    def workday_codes(self) -> list[str] | None:
        return [c for c in self.workdays.split(",") if c] if self.workdays else None

    @property
    def roles(self) -> set[RoleCode]:
        return {link.role.code for link in self.role_links}


class EmployeeRosterEntry(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Staff allowed to register: the admin uploads the list, a person types their number to join."""

    __tablename__ = "employee_roster"

    # Normalized digits: a mobile number as 09xxxxxxxxx, anything else (personnel code) as typed
    code: Mapped[str] = mapped_column(sa.String(20), unique=True, index=True)
    full_name: Mapped[str | None] = mapped_column(sa.String(120))
    hourly: Mapped[bool] = mapped_column(sa.Boolean, default=False, server_default=sa.false())
    # Set when someone registers with this number; one number, one account
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="SET NULL"), unique=True
    )
    claimed_at: Mapped[datetime | None] = mapped_column(utc_timestamp())


class Role(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "roles"

    code: Mapped[RoleCode] = mapped_column(enum_column(RoleCode), unique=True)
    title: Mapped[str] = mapped_column(sa.String(80))


class UserRole(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "user_roles"
    __table_args__ = (sa.UniqueConstraint("user_id", "role_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, sa.ForeignKey("users.id", ondelete="CASCADE"))
    role_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, sa.ForeignKey("roles.id", ondelete="CASCADE"))
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, sa.ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(utc_timestamp(), server_default=sa.func.now())

    user: Mapped[User] = relationship(back_populates="role_links", foreign_keys=[user_id])
    role: Mapped[Role] = relationship(lazy="joined")
