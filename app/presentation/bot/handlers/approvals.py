"""Generic approve/reject buttons for every workflow type.

Each workflow registers an `ApprovalHandler` under a short code (the `w` field of ApprovalCb).
Adding approvals to a new request type means writing one handler class and registering it here.
"""

from typing import Protocol
from uuid import UUID

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.application.presenters import leave as leave_texts
from app.application.services.container import Services
from app.core.exceptions import ExpiredActionError
from app.domain.approvals.engine import Decision
from app.domain.notifications.models import NotificationType
from app.domain.users.models import User
from app.presentation.bot.callbacks.data import ApprovalCb
from app.presentation.bot.handlers.common import show
from app.presentation.bot.handlers.leave import notify_approvers
from app.presentation.bot.keyboards.common import cancel_row, inline
from app.presentation.bot.middlewares.filters import IsRegistered
from app.presentation.bot.states.flows import ApprovalFlow

router = Router(name="approvals")
router.message.filter(IsRegistered())
router.callback_query.filter(IsRegistered())


class ApprovalHandler(Protocol):
    def reason_required(self, services: Services) -> bool: ...

    async def decide(
        self, services: Services, actor: User, entity_id: UUID, step: int, decision: Decision, reason: str | None
    ) -> str:
        """Apply the decision, notify the people involved, and return a status line for the admin."""
        ...


class LeaveApprovalHandler:
    def reason_required(self, services: Services) -> bool:
        return services.policy.leave.require_rejection_reason

    async def decide(
        self, services: Services, actor: User, entity_id: UUID, step: int, decision: Decision, reason: str | None
    ) -> str:
        result = await services.leave.decide(actor, entity_id, step, decision, reason)
        request, employee = result.request, result.employee
        if not result.approved:
            text = leave_texts.rejected_to_employee(request)
            admin_line = "❌ درخواست رد شد و به کارمند اطلاع داده شد."
        elif result.final:
            text = leave_texts.approved_to_employee(request)
            admin_line = "✅ درخواست تأیید نهایی شد و به کارمند اطلاع داده شد."
        else:
            text = leave_texts.advanced_to_employee(request)
            admin_line = "✅ تأیید شد و برای مرحله‌ی بعد ارسال شد."
        await services.notifications.notify_user(employee, text, NotificationType.LEAVE_STATUS_CHANGED)
        if result.next_step is not None:
            title = services.leave.workflow.step(result.next_step.step_order).title_fa
            sent = await notify_approvers(
                services, request, employee, result.next_approvers, result.next_step.step_order, title
            )
            if sent == 0:
                admin_line += "\n⚠️ کاربری با نقش مرحله‌ی بعد تعریف نشده است."
        return f"{leave_texts.request_details(request, employee)}\n\n{admin_line}"


HANDLERS: dict[str, ApprovalHandler] = {"lv": LeaveApprovalHandler()}


def _handler(code: str) -> ApprovalHandler:
    handler = HANDLERS.get(code)
    if handler is None:
        raise ExpiredActionError()
    return handler


@router.callback_query(ApprovalCb.filter(F.a == "y"))
async def approve(query: CallbackQuery, callback_data: ApprovalCb, services: Services, user: User) -> None:
    handler = _handler(callback_data.w)
    text = await handler.decide(services, user, UUID(hex=callback_data.e), callback_data.s, Decision.APPROVE, None)
    await show(query, text)


@router.callback_query(ApprovalCb.filter(F.a == "n"))
async def reject(
    query: CallbackQuery, callback_data: ApprovalCb, state: FSMContext, services: Services, user: User
) -> None:
    handler = _handler(callback_data.w)
    if not handler.reason_required(services):
        text = await handler.decide(services, user, UUID(hex=callback_data.e), callback_data.s, Decision.REJECT, None)
        await show(query, text)
        return
    await state.set_state(ApprovalFlow.typing_rejection_reason)
    await state.update_data(ap_w=callback_data.w, ap_e=callback_data.e, ap_s=callback_data.s)
    await query.answer()
    if isinstance(query.message, Message):
        await query.message.answer("✍️ لطفاً دلیل رد درخواست را بنویسید:", reply_markup=inline([cancel_row()]))


@router.message(StateFilter(ApprovalFlow.typing_rejection_reason), F.text)
async def rejection_reason(message: Message, state: FSMContext, services: Services, user: User) -> None:
    data = await state.get_data()
    handler = _handler(data.get("ap_w", ""))
    reason = (message.text or "").strip()
    minimum = services.policy.leave.min_rejection_reason_length
    if len(reason) < minimum:
        await message.answer("دلیل رد خیلی کوتاه است. لطفاً توضیح بیشتری بنویسید:")
        return
    await state.clear()
    text = await handler.decide(services, user, UUID(hex=data["ap_e"]), data["ap_s"], Decision.REJECT, reason)
    await message.answer(text)
