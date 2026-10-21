"""End-to-end handler tests: real aiogram Dispatcher, real services and DB, recorded Bot API calls."""

import itertools
from collections.abc import AsyncGenerator
from datetime import date, datetime
from typing import Any

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendDocument, SendMessage, TelegramMethod
from aiogram.types import CallbackQuery, Chat, Message, Update
from aiogram.types import User as TgUser

from app.application.services.container import Services
from app.core.security.rbac import RoleCode
from app.core.utils.clock import TimeService
from app.infrastructure.excel.menu_parser import parse_menu
from app.infrastructure.integrations.messenger import AiogramMessenger
from app.presentation.bot.callbacks.data import AttCb, CalCb, CorrCb, FoodCb, LeaveCb
from app.presentation.bot.dispatcher import build_dispatcher
from app.tests.conftest import tehran
from app.tests.test_food import VALID_ROWS, menu_file

_ids = itertools.count(1)


class RecordingSession(BaseSession):
    """Stands in for the HTTP session: records every Bot API call and returns plausible results."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod[Any]] = []
        self.blank_labels: list[str] = []
        self.widest_row = 0

    async def make_request(self, bot: Bot, method: TelegramMethod[Any], timeout: int | None = None) -> Any:  # noqa: ASYNC109
        self.calls.append(method)
        # Telegram, Bale and Soroush reject blank button labels. The bot's error middleware would
        # swallow an exception here, so violations are collected and checked after each test.
        for row in getattr(getattr(method, "reply_markup", None), "inline_keyboard", None) or []:
            self.blank_labels.extend(type(method).__name__ for b in row if not b.text.strip())
            self.widest_row = max(self.widest_row, len(row))
        if isinstance(method, (SendMessage, SendDocument)):
            return Message(
                message_id=next(_ids),
                date=datetime.now(),
                chat=Chat(id=int(method.chat_id), type="private"),
                text=getattr(method, "text", None),
            )
        return True

    async def stream_content(self, *args: Any, **kwargs: Any) -> AsyncGenerator[bytes, None]:
        yield b""

    async def close(self) -> None:
        pass

    def texts(self, chat_id: int | None = None) -> list[str]:
        out = []
        for call in self.calls:
            if isinstance(call, (SendMessage, EditMessageText)) and (chat_id is None or int(call.chat_id) == chat_id):
                out.append(call.text)
        return out

    def last_text(self, chat_id: int) -> str:
        return self.texts(chat_id)[-1]

    def documents(self, chat_id: int) -> list[SendDocument]:
        return [c for c in self.calls if isinstance(c, SendDocument) and int(c.chat_id) == chat_id]

    def alerts(self) -> list[str]:
        return [c.text for c in self.calls if isinstance(c, AnswerCallbackQuery) and c.text]

    def buttons(self, chat_id: int) -> list[str]:
        """callback_data of the inline buttons on the last message to chat_id."""
        for call in reversed(self.calls):
            if isinstance(call, (SendMessage, EditMessageText)) and int(call.chat_id) == chat_id:
                markup = call.reply_markup
                rows = getattr(markup, "inline_keyboard", None) or []
                return [b.callback_data for row in rows for b in row]
        return []


class Harness:
    def __init__(self, session_factory, policy, clock) -> None:
        self.recorder = RecordingSession()
        self.bot = Bot(token="42:TEST", session=self.recorder)
        self.time = TimeService(clock, policy.organization.timezone)
        self.messenger = AiogramMessenger(self.bot, max_retries=0)
        self.session_factory = session_factory
        self.policy = policy
        policy.rate_limit.min_interval_seconds = 0  # updates are fed back-to-back
        self.dp = build_dispatcher(MemoryStorage(), session_factory, policy, self.time, self.messenger, frozenset())
        self.update_ids = itertools.count(1)

    def __getattr__(self, name: str) -> Any:
        # texts / last_text / buttons / documents / alerts come from the recorder
        return getattr(self.recorder, name)

    def _user(self, uid: int) -> TgUser:
        return TgUser(id=uid, is_bot=False, first_name=f"u{uid}")

    async def text(self, uid: int, text: str) -> None:
        message = Message(
            message_id=next(_ids),
            date=datetime.now(),
            chat=Chat(id=uid, type="private"),
            from_user=self._user(uid),
            text=text,
        )
        await self.dp.feed_update(self.bot, Update(update_id=next(self.update_ids), message=message))

    async def press(self, uid: int, data: str) -> None:
        message = Message(message_id=next(_ids), date=datetime.now(), chat=Chat(id=uid, type="private"), text="…")
        query = CallbackQuery(
            id=str(next(_ids)), from_user=self._user(uid), chat_instance="c", data=data, message=message
        )
        await self.dp.feed_update(self.bot, Update(update_id=next(self.update_ids), callback_query=query))

    async def register(self, uid: int, name: str, *roles: RoleCode) -> None:
        await self.text(uid, "/start")
        await self.text(uid, name)
        if roles:
            async with self.session_factory() as session:
                services = Services(session, self.policy, self.time, self.messenger)
                user = await services.users.get_by_messenger_id(uid)
                for role in roles:
                    await services.users._link_role(user, role, assigned_by=None)
                await session.commit()

    def services(self, session) -> Services:
        return Services(session, self.policy, self.time, self.messenger)


@pytest.fixture
def bot(session_factory, policy, clock):
    harness = Harness(session_factory, policy, clock)
    yield harness
    assert not harness.recorder.blank_labels, f"blank inline button labels sent: {harness.recorder.blank_labels}"


EMP, SEC, MGR = 5001, 5002, 5003


async def test_registration_and_main_menu(bot: Harness) -> None:
    await bot.text(EMP, "سلام")
    assert "/start" in bot.last_text(EMP)
    await bot.text(EMP, "/start")
    assert "نام و نام خانوادگی" in bot.last_text(EMP)
    await bot.text(EMP, "12")
    assert "به‌درستی" in bot.last_text(EMP) or bot.alerts() == []
    await bot.text(EMP, "مریم کریمی")
    assert "ثبت‌نام شما" in bot.last_text(EMP)
    await bot.text(EMP, "/myid")
    assert str(EMP) in bot.last_text(EMP)


async def test_check_in_and_out_through_buttons(bot: Harness, clock) -> None:
    await bot.register(EMP, "مریم کریمی")
    clock.set(tehran(2026, 10, 19, 9, 3))
    await bot.text(EMP, "⏱ ورود و خروج")
    assert AttCb(a="in").pack() in bot.buttons(EMP)
    await bot.press(EMP, AttCb(a="in").pack())
    assert "آیا ورود" in bot.last_text(EMP)
    confirm = next(b for b in bot.buttons(EMP) if b.startswith("att:in_ok"))
    await bot.press(EMP, confirm)
    assert "ورود شما ثبت شد" in bot.last_text(EMP)
    await bot.press(EMP, confirm)  # double tap on the old button
    assert "قبلاً" in bot.last_text(EMP)

    clock.set(tehran(2026, 10, 19, 17, 30))
    await bot.press(EMP, AttCb(a="out").pack())
    confirm = next(b for b in bot.buttons(EMP) if b.startswith("att:out_ok"))
    await bot.press(EMP, confirm)
    assert "خروج شما ثبت شد" in bot.last_text(EMP)
    await bot.press(EMP, AttCb(a="status").pack())
    assert "کارکرد" in bot.last_text(EMP)
    await bot.press(EMP, AttCb(a="week").pack())
    assert "گزارش هفتگی من" in bot.last_text(EMP)
    await bot.press(EMP, AttCb(a="week_xls").pack())
    assert bot.documents(EMP)


async def test_forgotten_entry_flow_with_typed_minutes(bot: Harness, clock) -> None:
    await bot.register(EMP, "مریم کریمی")
    clock.set(tehran(2026, 10, 19, 12, 0))
    await bot.press(EMP, AttCb(a="forgot").pack())
    assert "cor:kind:both" not in bot.buttons(EMP)  # «هر دو» was removed
    await bot.press(EMP, "cor:kind:in")
    # The day picker lists real dates (no placeholder grid) and can page back in time.
    assert CalCb(p="corr", a="d", v=date(2026, 10, 18).toordinal()).pack() in bot.buttons(EMP)
    back = next(b for b in bot.buttons(EMP) if b.startswith("cal:corr:m:"))
    await bot.press(EMP, back)
    assert any(b.startswith("cal:corr:d:") for b in bot.buttons(EMP))
    await bot.press(EMP, CalCb(p="corr", a="d", v=date(2026, 10, 18).toordinal()).pack())
    assert "ساعت ورود" in bot.last_text(EMP)
    await bot.text(EMP, "۰۸:۴۰")
    assert "مرور ثبت فراموشی" in bot.last_text(EMP)
    await bot.press(EMP, CorrCb(a="ok").pack())
    assert "ثبت فراموشی انجام شد" in bot.last_text(EMP)
    await bot.press(EMP, CorrCb(a="ok").pack())  # stale confirm
    assert any("منقضی" in a for a in bot.alerts())

    # Forgotten check-out for the same day, with a typed Jalali date and hour/minute buttons.
    await bot.press(EMP, AttCb(a="forgot").pack())
    await bot.press(EMP, "cor:kind:out")
    await bot.text(EMP, "۱۴۰۵/۰۸/۳۰")  # outside the allowed window
    assert "قابل انتخاب نیست" in bot.last_text(EMP)
    await bot.text(EMP, "۱۴۰۵/۰۷/۲۶")
    assert "ساعت خروج" in bot.last_text(EMP)
    await bot.press(EMP, "tm:corr_out:h:17:0")
    await bot.press(EMP, "tm:corr_out:m:17:15")
    await bot.press(EMP, CorrCb(a="ok").pack())
    assert "ثبت فراموشی انجام شد" in bot.last_text(EMP)
    await bot.press(EMP, AttCb(a="week").pack())
    assert "۰۸:۴۰" in bot.last_text(EMP) and "۱۷:۱۵" in bot.last_text(EMP)


async def test_hourly_leave_with_typed_date(bot: Harness, clock) -> None:
    await bot.register(EMP, "مریم کریمی")
    await bot.register(SEC, "سارا منشی", RoleCode.ADMIN_1_SECRETARY)
    clock.set(tehran(2026, 10, 19, 8, 0))
    await bot.press(EMP, LeaveCb(a="new").pack())
    await bot.press(EMP, LeaveCb(a="pay", v="UNPAID").pack())
    await bot.press(EMP, LeaveCb(a="unit", v="HOURLY").pack())
    assert any(b.startswith("cal:lv_d:d:") for b in bot.buttons(EMP))
    await bot.text(EMP, "1405/07/29")
    assert "ساعت شروع" in bot.last_text(EMP)
    await bot.text(EMP, "10:00")
    await bot.text(EMP, "12:30")
    await bot.press(EMP, LeaveCb(a="skip_reason").pack())
    assert "۲:۳۰ ساعت" in bot.last_text(EMP)
    await bot.press(EMP, LeaveCb(a="submit").pack())
    assert "درخواست جدید برای بررسی" in bot.last_text(SEC)


async def test_leave_request_two_step_approval_and_rejection(bot: Harness, clock) -> None:
    await bot.register(EMP, "مریم کریمی")
    await bot.register(SEC, "سارا منشی", RoleCode.ADMIN_1_SECRETARY)
    await bot.register(MGR, "رضا مدیر", RoleCode.ADMIN_2_MANAGER)
    clock.set(tehran(2026, 10, 19, 8, 0))

    async def submit_daily(day: date) -> None:
        await bot.press(EMP, LeaveCb(a="new").pack())
        await bot.press(EMP, LeaveCb(a="pay", v="PAID").pack())
        await bot.press(EMP, LeaveCb(a="unit", v="DAILY").pack())
        await bot.press(EMP, CalCb(p="lv_s", a="d", v=day.toordinal()).pack())
        await bot.press(EMP, CalCb(p="lv_e", a="d", v=day.toordinal()).pack())
        await bot.text(EMP, "کار شخصی")
        assert "مرور درخواست مرخصی" in bot.last_text(EMP)
        await bot.press(EMP, LeaveCb(a="submit").pack())

    await submit_daily(date(2026, 10, 21))
    assert "ثبت شد" in bot.texts(EMP)[-1]
    # The secretary got the request with approve/reject buttons; the manager got nothing yet.
    assert "درخواست جدید برای بررسی" in bot.last_text(SEC)
    assert not any("درخواست جدید" in t for t in bot.texts(MGR))
    approve = next(b for b in bot.buttons(SEC) if b.endswith(":y"))
    await bot.press(SEC, approve)
    assert "توسط منشی تأیید شد" in bot.last_text(EMP)
    assert "درخواست جدید برای بررسی" in bot.last_text(MGR)
    await bot.press(SEC, approve)  # stale second click
    assert any("قبلاً" in a for a in bot.alerts())

    reject = next(b for b in bot.buttons(MGR) if b.endswith(":n"))
    await bot.press(MGR, reject)
    assert "دلیل رد" in bot.last_text(MGR)
    await bot.text(MGR, "در این تاریخ جلسه‌ی مهم داریم")
    assert "رد شد" in bot.last_text(EMP) and "جلسه" in bot.last_text(EMP)

    # Overlap with a rejected request is fine; the history shows both.
    await submit_daily(date(2026, 10, 21))
    await bot.press(EMP, LeaveCb(a="mine").pack())
    assert "رد شده" in bot.last_text(EMP) and "در انتظار بررسی منشی" in bot.last_text(EMP)


async def test_food_order_and_admin_reports(bot: Harness, clock) -> None:
    await bot.register(EMP, "مریم کریمی")
    await bot.register(SEC, "سارا منشی", RoleCode.ADMIN_1_SECRETARY)
    clock.set(tehran(2026, 10, 19, 8, 0))
    async with bot.session_factory() as session:
        services = bot.services(session)
        secretary = await services.users.get_by_messenger_id(SEC)
        parsed = parse_menu(menu_file(VALID_ROWS), date(2026, 10, 19), 100_000_000)
        menu = (await services.food.import_menu(secretary, parsed, "menu.xlsx", None)).menu
        item_id = next(i.id for i in menu.items if i.food_name == "جوجه کباب")

    await bot.text(EMP, "🍽 سفارش غذا")
    await bot.press(EMP, FoodCb(a="menu").pack())
    assert "منوی این هفته" in bot.last_text(EMP)
    await bot.press(EMP, FoodCb(a="today").pack())
    assert FoodCb(a="item", v=item_id.hex).pack() in bot.buttons(EMP)
    await bot.press(EMP, FoodCb(a="item", v=item_id.hex).pack())
    await bot.press(EMP, FoodCb(a="ok", v=item_id.hex).pack())
    assert "ثبت شد" in bot.last_text(EMP)
    await bot.press(EMP, FoodCb(a="g_item", v=item_id.hex).pack())
    await bot.text(EMP, "۲")
    assert "سفارش مهمان" in bot.last_text(EMP)
    await bot.press(EMP, FoodCb(a="g_ok", v=f"{item_id.hex}.2").pack())
    await bot.press(EMP, FoodCb(a="invoice").pack())
    assert "۶۳۰٬۰۰۰" in bot.last_text(EMP)  # 1 + 2 guests × 210,000

    clock.set(tehran(2026, 10, 19, 10, 30))
    await bot.press(EMP, FoodCb(a="ok", v=item_id.hex).pack())
    assert any("مهلت" in a for a in bot.alerts())

    await bot.press(SEC, "fa:summary:0")
    assert "جوجه کباب: ۱ پرس" in bot.last_text(SEC) and "مهمان: ۲ پرس" in bot.last_text(SEC)
    await bot.press(SEC, "fa:orders_xls:")
    await bot.press(SEC, "fa:invoices:")
    assert len(bot.documents(SEC)) == 2
    await bot.press(SEC, "fa:template:")
    assert len(bot.documents(SEC)) == 3
    await bot.press(EMP, "fa:invoices:")  # employees are refused
    assert any("اجازه" in a for a in bot.alerts())


async def test_admin_attendance_report_and_role_management(bot: Harness, clock) -> None:
    await bot.register(EMP, "مریم کریمی")
    await bot.register(SEC, "سارا منشی", RoleCode.SUPER_ADMIN)
    clock.set(tehran(2026, 10, 19, 11, 0))
    await bot.text(SEC, "🛠 پنل مدیریت")
    await bot.press(SEC, "adm:att:today")
    assert "گزارش حضور امروز" in bot.texts(SEC)[-1]
    assert bot.documents(SEC)
    await bot.press(EMP, "adm:att:today")
    assert any("اجازه" in a for a in bot.alerts())

    async with bot.session_factory() as session:
        target = await bot.services(session).users.get_by_messenger_id(EMP)
    await bot.press(SEC, f"ua:role:{target.id.hex}:ADMIN_2_MANAGER:1")
    assert "نقش‌ها به‌روز شد" in bot.last_text(SEC)
    assert "اضافه شد" in bot.last_text(EMP)
    await bot.text(SEC, f"/user {EMP}")
    assert "ادمین ۲" in bot.last_text(SEC)


async def test_soroush_row_limit_applies_to_every_keyboard(bot: Harness, clock) -> None:
    from app.infrastructure.integrations.messenger import set_max_buttons_per_row

    set_max_buttons_per_row(2)  # what create_bot() sets for BOT_PLATFORM=soroush
    try:
        await bot.register(EMP, "مریم کریمی")
        clock.set(tehran(2026, 10, 19, 12, 0))
        await bot.press(EMP, AttCb(a="forgot").pack())
        await bot.press(EMP, "cor:kind:in")
        await bot.press(EMP, CalCb(p="corr", a="d", v=date(2026, 10, 18).toordinal()).pack())
        await bot.press(EMP, "tm:corr_in:h:8:0")  # minute picker
        await bot.press(EMP, "tm:corr_in:m:8:15")
        await bot.press(EMP, LeaveCb(a="new").pack())
        await bot.press(EMP, LeaveCb(a="pay", v="PAID").pack())
        await bot.press(EMP, LeaveCb(a="unit", v="HOURLY").pack())
        await bot.press(EMP, CalCb(p="lv_d", a="d", v=date(2026, 10, 20).toordinal()).pack())  # hour picker
        assert "tm:lv_s:h:10:0" in bot.buttons(EMP)
        assert bot.recorder.widest_row == 2
    finally:
        set_max_buttons_per_row(8)
