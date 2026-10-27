"""Typed organization policy loaded from YAML.

`config/default.yaml` is always loaded. `config/<APP_ENV>.yaml` and the file named by
POLICY_FILE are deep-merged over it, so an environment only lists what it changes.
"""

from datetime import time
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

WEEKDAY_CODES: dict[str, int] = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _parse_time(value: Any) -> time | None:
    if value is None or value == "":
        return None
    if isinstance(value, time):
        return value
    hour, minute = str(value).strip().split(":")
    return time(int(hour), int(minute))


class OrganizationPolicy(BaseModel):
    name: str = "Organization"
    timezone: str = "Asia/Tehran"
    locale: str = "fa_IR"
    holidays: list[str] = Field(default_factory=list)

    @field_validator("holidays")
    @classmethod
    def _check_holidays(cls, values: list[str]) -> list[str]:
        for value in values:
            parts = str(value).split("/")
            if len(parts) != 3 or not all(p.isdigit() for p in parts):
                raise ValueError(f"holiday must look like 1405/01/01, got {value!r}")
        return [str(v) for v in values]


class UsersPolicy(BaseModel):
    require_admin_activation: bool = False
    min_full_name_length: int = Field(default=3, ge=1)
    max_full_name_length: int = Field(default=80, ge=3)


class AttendancePolicy(BaseModel):
    workdays: list[str] = Field(default_factory=lambda: ["sat", "sun", "mon", "tue", "wed", "thu"])
    standard_workday_minutes: int = Field(default=480, ge=0)
    standard_start_time: time = time(9, 0)
    standard_end_time: time = time(17, 0)
    check_in_reminder_time: time = time(10, 0)
    check_out_reminder_time: time = time(20, 0)
    reminder_catchup_minutes: int = Field(default=180, ge=0)
    allowed_late_arrival_minutes: int = Field(default=0, ge=0)
    allowed_early_departure_minutes: int = Field(default=0, ge=0)
    overtime_rounding_minutes: int = Field(default=15, ge=0)
    calculation_policy: str = "standard_deduction"
    # floating_window policy: arriving between standard_start_time and this time is allowed lateness
    floating_check_in_until: time | None = None
    # Monthly caps shown in month reports (0 = no cap)
    monthly_unauthorized_late_cap_minutes: int = Field(default=0, ge=0)
    monthly_early_departure_cap_minutes: int = Field(default=0, ge=0)
    # کارکرد نهایی = normal + MIN(overtime, compensable) + paid leave
    #               + overtime surplus × surplus factor − uncompensated × penalty factor
    final_work_surplus_factor: float = Field(default=0.8, ge=0)
    final_work_penalty_factor: float = Field(default=1.6, ge=0)
    max_shift_hours: int = Field(default=16, ge=1, le=24)
    allow_checkout_without_checkin: bool = False
    live_confirmation_ttl_seconds: int = Field(default=300, ge=10)
    allow_same_day_manual_correction: bool = False
    allow_future_manual_correction: bool = False
    max_manual_correction_age_days: int = Field(default=30, ge=0)
    manual_correction_requires_approval: bool = False

    _parse_times = field_validator(
        "standard_start_time",
        "standard_end_time",
        "check_in_reminder_time",
        "check_out_reminder_time",
        "floating_check_in_until",
        mode="before",
    )(_parse_time)

    @field_validator("workdays")
    @classmethod
    def _check_workdays(cls, values: list[str]) -> list[str]:
        normalized = [v.strip().lower()[:3] for v in values]
        unknown = [v for v in normalized if v not in WEEKDAY_CODES]
        if unknown:
            raise ValueError(f"unknown weekday codes: {unknown}")
        return normalized

    @model_validator(mode="after")
    def _check_schedule(self) -> "AttendancePolicy":
        if self.standard_end_time <= self.standard_start_time:
            raise ValueError("standard_end_time must be after standard_start_time")
        if self.floating_check_in_until and not (
            self.standard_start_time <= self.floating_check_in_until < self.standard_end_time
        ):
            raise ValueError("floating_check_in_until must be between standard_start_time and standard_end_time")
        return self

    @property
    def workday_numbers(self) -> frozenset[int]:
        """Python weekday numbers (Monday = 0)."""
        return frozenset(WEEKDAY_CODES[d] for d in self.workdays)


class LeavePolicy(BaseModel):
    require_rejection_reason: bool = True
    allow_overlapping_leave_requests: bool = False
    max_past_days: int = Field(default=7, ge=0)
    max_future_days: int = Field(default=90, ge=0)
    max_daily_leave_days: int = Field(default=30, ge=1)
    count_non_workdays_in_daily_leave: bool = True
    min_rejection_reason_length: int = Field(default=3, ge=1)
    max_reason_length: int = Field(default=500, ge=10)


class FoodPolicy(BaseModel):
    ordering_deadline_time: time = time(10, 0)
    guest_ordering_deadline_time: time | None = None
    currency_label: str = "تومان"
    allow_guest_orders: bool = True
    max_guest_count_per_order: int = Field(default=10, ge=1)
    max_items_per_employee_order: int = Field(default=1, ge=1)
    allow_order_edit_before_deadline: bool = True
    max_price_toman: int = Field(default=100_000_000, gt=0)
    retain_menu_files: bool = False

    @field_validator("ordering_deadline_time", mode="before")
    @classmethod
    def _deadline(cls, value: Any) -> time:
        return _parse_time(value)

    @field_validator("guest_ordering_deadline_time", mode="before")
    @classmethod
    def _guest_deadline(cls, value: Any) -> time | None:
        if value in (None, ""):
            return None
        return _parse_time(value)

    @property
    def guest_deadline(self) -> time:
        return self.guest_ordering_deadline_time or self.ordering_deadline_time


class PaymentPolicy(BaseModel):
    shaba_number: str = ""
    account_holder_name: str = ""
    payment_message: str = ""


class NotificationPolicy(BaseModel):
    batch_size: int = Field(default=20, ge=1)
    batch_interval_seconds: float = Field(default=1.5, ge=0)
    max_send_retries: int = Field(default=3, ge=0)


class ReportsPolicy(BaseModel):
    retention_hours: int = Field(default=24, ge=1)


class RateLimitPolicy(BaseModel):
    min_interval_seconds: float = Field(default=0.4, ge=0)


class Policy(BaseModel):
    organization: OrganizationPolicy = Field(default_factory=OrganizationPolicy)
    users: UsersPolicy = Field(default_factory=UsersPolicy)
    attendance: AttendancePolicy = Field(default_factory=AttendancePolicy)
    leave: LeavePolicy = Field(default_factory=LeavePolicy)
    food: FoodPolicy = Field(default_factory=FoodPolicy)
    payment: PaymentPolicy = Field(default_factory=PaymentPolicy)
    notifications: NotificationPolicy = Field(default_factory=NotificationPolicy)
    reports: ReportsPolicy = Field(default_factory=ReportsPolicy)
    rate_limit: RateLimitPolicy = Field(default_factory=RateLimitPolicy)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a mapping at the top level")
    return data


def load_policy(policy_dir: Path, app_env: str = "", extra_file: str = "") -> Policy:
    data: dict[str, Any] = {}
    candidates = [policy_dir / "default.yaml"]
    if app_env:
        candidates.append(policy_dir / f"{app_env}.yaml")
    if extra_file:
        candidates.append(Path(extra_file))
    for path in candidates:
        if path.exists():
            data = _deep_merge(data, _read_yaml(path))
        elif path == Path(extra_file):
            raise FileNotFoundError(f"POLICY_FILE not found: {path}")
    return Policy.model_validate(data)


@lru_cache
def get_policy() -> Policy:
    from app.core.config.settings import get_settings

    settings = get_settings()
    return load_policy(settings.policy_dir, settings.app_env, settings.policy_file)
