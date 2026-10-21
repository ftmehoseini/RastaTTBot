"""Per-unit-of-work service factory. One container per bot update or background job."""

from functools import cached_property

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.dto.messaging import Messenger
from app.application.services.attendance_service import AttendanceService
from app.application.services.food_service import FoodService
from app.application.services.leave_service import LeaveService
from app.application.services.notification_service import NotificationService
from app.application.services.user_service import UserService
from app.core.config.policy import Policy
from app.core.utils.clock import TimeService


class Services:
    def __init__(
        self,
        session: AsyncSession,
        policy: Policy,
        time: TimeService,
        messenger: Messenger,
        bootstrap_super_admin_ids: frozenset[int] = frozenset(),
    ):
        self.session = session
        self.policy = policy
        self.time = time
        self.messenger = messenger
        self._bootstrap_ids = bootstrap_super_admin_ids

    @cached_property
    def users(self) -> UserService:
        return UserService(self.session, self.policy.users, self.time, self._bootstrap_ids)

    @cached_property
    def attendance(self) -> AttendanceService:
        return AttendanceService(self.session, self.policy, self.time)

    @cached_property
    def leave(self) -> LeaveService:
        return LeaveService(self.session, self.policy, self.time)

    @cached_property
    def food(self) -> FoodService:
        return FoodService(self.session, self.policy, self.time)

    @cached_property
    def notifications(self) -> NotificationService:
        return NotificationService(self.session, self.messenger, self.time, self.policy.notifications)
