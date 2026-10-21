"""Time source and UTC ↔ local conversion.

Every timestamp in the database is timezone-aware UTC. Business dates (work date,
meal date, leave date) are local dates in the organization timezone. All conversion
goes through `TimeService` so no code ever uses a naive server-local datetime.
"""

from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo


class Clock(Protocol):
    def now_utc(self) -> datetime: ...


class SystemClock:
    def now_utc(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """Test clock. Accepts aware datetimes in any zone and stores UTC."""

    def __init__(self, at: datetime):
        self.set(at)

    def set(self, at: datetime) -> None:
        if at.tzinfo is None:
            raise ValueError("FixedClock needs an aware datetime")
        self._now = at.astimezone(UTC)

    def advance(self, **kwargs: float) -> None:
        self._now += timedelta(**kwargs)

    def now_utc(self) -> datetime:
        return self._now


class TimeService:
    def __init__(self, clock: Clock, timezone: str = "Asia/Tehran"):
        self.clock = clock
        self.tz = ZoneInfo(timezone)

    def now_utc(self) -> datetime:
        return self.clock.now_utc()

    def now_local(self) -> datetime:
        return self.clock.now_utc().astimezone(self.tz)

    def today(self) -> date:
        return self.now_local().date()

    def to_local(self, moment: datetime) -> datetime:
        if moment.tzinfo is None:
            # SQLite (tests) drops tzinfo; values written by this app are always UTC.
            moment = moment.replace(tzinfo=UTC)
        return moment.astimezone(self.tz)

    def local_to_utc(self, day: date, at: time) -> datetime:
        return datetime.combine(day, at, tzinfo=self.tz).astimezone(UTC)

    def local_day_bounds_utc(self, day: date) -> tuple[datetime, datetime]:
        """[start, end) of a local calendar day, in UTC."""
        return self.local_to_utc(day, time.min), self.local_to_utc(day + timedelta(days=1), time.min)


def ensure_utc(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)
