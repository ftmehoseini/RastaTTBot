import io
from datetime import date

import pytest
from openpyxl import Workbook

from app.application.services.container import Services
from app.core.exceptions import DeadlinePassedError, PermissionDeniedError, ValidationError
from app.core.security.rbac import RoleCode
from app.domain.food.models import OrderStatus
from app.infrastructure.excel.menu_parser import build_menu_template, parse_menu
from app.tests.conftest import tehran

TODAY = date(2026, 10, 19)  # Monday; week is Sat 2026-10-17 … Fri 2026-10-23


def menu_file(rows: list[list], headers: list[str] | None = None) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(headers or ["jalali_date", "weekday_name", "food_name", "price_toman", "is_active"])
    for row in rows:
        ws.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


VALID_ROWS = [
    ["1405/07/27", "دوشنبه", "قرمه‌سبزی", 185000, 1],
    ["1405/07/27", "دوشنبه", "جوجه کباب", "210,000", 1],
    ["1405/07/28", "سه شنبه", "قیمه", "۱۷۵۰۰۰", 1],
    ["1405/07/29", "چهارشنبه", "ماکارونی", 150000, 0],
]


# --- menu parser ------------------------------------------------------------------------------------


def test_parse_valid_menu() -> None:
    result = parse_menu(menu_file(VALID_ROWS), TODAY, 100_000_000)
    assert result.ok, result.errors
    assert result.week_start == date(2026, 10, 17)
    assert [r.price_toman for r in result.rows] == [185000, 210000, 175000, 150000]
    assert result.rows[3].is_active is False


def test_parse_accepts_persian_headers() -> None:
    content = menu_file([["1405/07/27", "قرمه‌سبزی", 185000]], headers=["تاریخ", "نام غذا", "قیمت (تومان)"])
    assert parse_menu(content, TODAY, 100_000_000).ok


@pytest.mark.parametrize(
    ("rows", "fragment"),
    [
        ([["1405/07/32", "", "قیمه", 1000, 1]], "تاریخ نامعتبر"),
        ([["1405/07/27", "شنبه", "قیمه", 1000, 1]], "هم‌خوانی ندارد"),
        ([["1405/07/27", "", "", 1000, 1]], "نام غذا خالی"),
        ([["1405/07/27", "", "قیمه", "رایگان", 1]], "قیمت"),
        ([["1405/07/27", "", "قیمه", -5, 1]], "قیمت"),
        ([["1405/07/27", "", "قیمه", 1000, "شاید"]], "is_active"),
        ([["1405/07/27", "", "قیمه", 1000, 1], ["1405/07/27", "", "قیمه", 2000, 1]], "تکراری"),
        ([["1405/07/27", "", "قیمه", 1000, 1], ["1405/08/10", "", "قیمه", 1000, 1]], "یک هفته"),
        ([["1405/07/10", "", "قیمه", 1000, 1]], "تمام شده"),
        ([], "هیچ ردیف"),
    ],
)
def test_parse_rejects_invalid_rows(rows: list[list], fragment: str) -> None:
    result = parse_menu(menu_file(rows), TODAY, 100_000_000)
    assert not result.ok
    assert any(fragment in e for e in result.errors), result.errors


def test_parse_rejects_bad_files_and_headers() -> None:
    assert "معتبر نیست" in parse_menu(b"not an excel file", TODAY, 1000).errors[0]
    result = parse_menu(menu_file([["x"]], headers=["date", "dish"]), TODAY, 1000)
    assert "ستون‌های لازم" in result.errors[0]


def test_template_round_trip() -> None:
    content = build_menu_template(date(2026, 10, 24))
    result = parse_menu(content, TODAY, 100_000_000)
    # The empty template fails only because dishes are not filled in yet.
    assert result.errors and all("نام غذا خالی" in e for e in result.errors)


# --- ordering ----------------------------------------------------------------------------------------


@pytest.fixture
async def menu(services: Services, make_user):
    secretary = await make_user("منشی", RoleCode.FOOD_ADMIN)
    parsed = parse_menu(menu_file(VALID_ROWS), TODAY, 100_000_000)
    result = await services.food.import_menu(secretary, parsed, "menu.xlsx", None)
    return {"secretary": secretary, "menu": result.menu, "items": {i.food_name: i for i in result.menu.items}}


async def test_order_and_replace_choice_before_deadline(services, make_user, menu, clock) -> None:
    user = await make_user()
    clock.set(tehran(2026, 10, 19, 9, 0))
    first = await services.food.place_order(user, menu["items"]["قرمه‌سبزی"].id)
    assert first.changed and first.order.total_price == 185000
    same = await services.food.place_order(user, menu["items"]["قرمه‌سبزی"].id)
    assert not same.changed  # double tap does not duplicate
    replaced = await services.food.place_order(user, menu["items"]["جوجه کباب"].id)
    assert [i.food_name for i in replaced.order.items] == ["جوجه کباب"]
    assert replaced.order.id == first.order.id  # still one order for the day


async def test_deadline_blocks_orders_and_cancellation(services, make_user, menu, clock) -> None:
    user = await make_user()
    clock.set(tehran(2026, 10, 19, 9, 59))
    await services.food.place_order(user, menu["items"]["قرمه‌سبزی"].id)
    clock.set(tehran(2026, 10, 19, 10, 0))
    with pytest.raises(DeadlinePassedError):
        await services.food.place_order(user, menu["items"]["جوجه کباب"].id)
    with pytest.raises(DeadlinePassedError):
        await services.food.cancel_order(user, TODAY)
    # Tomorrow is still open.
    order = await services.food.place_order(user, menu["items"]["قیمه"].id)
    assert order.order.order_date == date(2026, 10, 20)


async def test_inactive_items_cannot_be_ordered(services, make_user, menu) -> None:
    user = await make_user()
    from app.core.exceptions import NotFoundError

    with pytest.raises(NotFoundError):
        await services.food.place_order(user, menu["items"]["ماکارونی"].id)


async def test_guest_orders_and_invoice(services, make_user, menu, clock, policy) -> None:
    services.policy.food.allow_guest_orders = True  # off in the settings; the code is kept
    user = await make_user()
    clock.set(tehran(2026, 10, 19, 8, 0))
    await services.food.place_order(user, menu["items"]["قرمه‌سبزی"].id)
    guest = await services.food.place_guest_order(user, menu["items"]["جوجه کباب"].id, 3)
    assert guest.total_price == 630000
    updated = await services.food.place_guest_order(user, menu["items"]["جوجه کباب"].id, 2)
    assert updated.id == guest.id and updated.guest_count == 2
    with pytest.raises(ValidationError):
        await services.food.place_guest_order(
            user, menu["items"]["جوجه کباب"].id, policy.food.max_guest_count_per_order + 1
        )

    invoice = await services.food.invoice(user)
    assert invoice.employee_meals == 1 and invoice.employee_amount == 185000
    assert invoice.guest_meals == 2 and invoice.guest_amount == 420000
    assert invoice.total == 605000

    summaries = await services.food.day_summaries(menu["secretary"], date(2026, 10, 17), date(2026, 10, 23))
    monday = summaries[0]
    assert monday.employee_counts == {"قرمه‌سبزی": 1}
    assert monday.guest_counts == {"جوجه کباب": 2}
    assert monday.employee_total + monday.guest_total == 3

    invoices = await services.food.team_invoices(menu["secretary"])
    assert [i.total for i in invoices] == [605000]


async def test_menu_replacement_keeps_existing_orders(services, make_user, menu, clock) -> None:
    user = await make_user()
    clock.set(tehran(2026, 10, 19, 8, 0))
    await services.food.place_order(user, menu["items"]["قیمه"].id)  # Tuesday
    await services.food.place_order(user, menu["items"]["جوجه کباب"].id)  # Monday
    new_rows = [
        ["1405/07/27", "دوشنبه", "جوجه کباب", 999000, 1],  # price changed
        ["1405/07/28", "سه شنبه", "عدس پلو", 120000, 1],  # قیمه removed
    ]
    parsed = parse_menu(menu_file(new_rows), TODAY, 100_000_000)
    preview = await services.food.preview_import(menu["secretary"], parsed)
    assert preview.current_version == 1 and preview.affected_orders == 1 and preview.price_changes == 1
    result = await services.food.import_menu(menu["secretary"], parsed, "v2.xlsx", None)
    assert result.menu.version == 2 and result.remapped_orders == 1 and result.orphaned_orders == 1

    monday = await services.food.get_order(user, TODAY)
    assert monday.items[0].unit_price_toman == 210000  # ordered price is kept
    tuesday = await services.food.get_order(user, date(2026, 10, 20))
    assert tuesday.status == OrderStatus.ACTIVE and tuesday.items[0].food_name == "قیمه"


async def test_only_menu_managers_can_import(services, make_user) -> None:
    employee = await make_user()
    parsed = parse_menu(menu_file(VALID_ROWS), TODAY, 100_000_000)
    with pytest.raises(PermissionDeniedError):
        await services.food.import_menu(employee, parsed, "x.xlsx", None)
    manager = await make_user("مدیر", RoleCode.ADMIN_2_MANAGER)
    with pytest.raises(PermissionDeniedError):  # managers see reports but do not edit menus
        await services.food.import_menu(manager, parsed, "x.xlsx", None)


# --- the whole week in one go -------------------------------------------------------------------------


def _items(menu) -> dict[str, object]:
    return {i.food_name: i for i in menu["menu"].items}


async def test_save_week_sets_each_day_and_reports_what_it_could_not(services, make_user, menu, clock) -> None:
    user = await make_user()
    items = _items(menu)
    clock.set(tehran(2026, 10, 19, 9, 0))
    first = await services.food.save_week(
        user, {date(2026, 10, 19): [items["قرمه‌سبزی"].id], date(2026, 10, 20): [items["قیمه"].id]}
    )
    assert [d for d, _ in first.saved] == [date(2026, 10, 19), date(2026, 10, 20)]
    again = await services.food.save_week(user, {date(2026, 10, 19): [items["قرمه‌سبزی"].id], date(2026, 10, 20): []})
    assert again.unchanged == [date(2026, 10, 19)] and again.cancelled == [date(2026, 10, 20)]
    clock.set(tehran(2026, 10, 19, 10, 5))  # 27 مهر is closed now
    late = await services.food.save_week(user, {date(2026, 10, 19): [items["جوجه کباب"].id]})
    assert not late.changed and late.skipped and late.skipped[0][0] == date(2026, 10, 19)
    current = await services.food.current_choices(user, [date(2026, 10, 19)])
    assert current == {date(2026, 10, 19): [items["قرمه‌سبزی"].id]}


async def test_save_week_follows_the_food_settings(services, make_user, menu, clock) -> None:
    user = await make_user()
    items = _items(menu)
    clock.set(tehran(2026, 10, 19, 9, 0))
    services.policy.food.max_items_per_employee_order = 2
    both = [items["قرمه‌سبزی"].id, items["جوجه کباب"].id]
    assert (await services.food.save_week(user, {date(2026, 10, 19): both})).saved
    assert sorted(
        map(str, (await services.food.current_choices(user, [date(2026, 10, 19)]))[date(2026, 10, 19)])
    ) == sorted(map(str, both))
    services.policy.food.allow_order_edit_before_deadline = False
    blocked = await services.food.save_week(user, {date(2026, 10, 19): [items["قرمه‌سبزی"].id]})
    assert not blocked.changed and "مجاز نیست" in blocked.skipped[0][1]


async def test_save_guest_week_sets_counts_cancels_and_reports(services, make_user, menu, clock, policy) -> None:
    services.policy.food.allow_guest_orders = True  # off in the settings; the code is kept
    user = await make_user()
    items = _items(menu)
    joojeh, gheimeh = items["جوجه کباب"].id, items["قیمه"].id
    clock.set(tehran(2026, 10, 19, 9, 0))
    result = await services.food.save_guest_week(user, {joojeh: 3, gheimeh: 2})
    assert [(n, c) for _, n, c in result.saved] == [("جوجه کباب", 3), ("قیمه", 2)]
    days = [date(2026, 10, 19), date(2026, 10, 20)]
    assert await services.food.current_guest_counts(user, days) == {joojeh: 3, gheimeh: 2}

    over = policy.food.max_guest_count_per_order + 1
    result = await services.food.save_guest_week(user, {joojeh: 3, gheimeh: 0})
    assert result.cancelled == [(date(2026, 10, 20), "قیمه")] and not result.saved
    result = await services.food.save_guest_week(user, {joojeh: over})
    assert not result.changed and "حداکثر" in result.skipped[0][2]

    clock.set(tehran(2026, 10, 19, 10, 30))  # Monday closed, Tuesday still open
    result = await services.food.save_guest_week(user, {joojeh: 1, gheimeh: 4})
    assert result.skipped[0][:2] == (date(2026, 10, 19), "جوجه کباب") and result.saved[0][1:] == ("قیمه", 4)
    assert await services.food.current_guest_counts(user, days) == {joojeh: 3, gheimeh: 4}


async def test_a_new_menu_without_an_ordered_dish_tells_only_those_people(
    services, make_user, menu, clock, messenger
) -> None:
    services.policy.food.allow_guest_orders = True  # off in the settings; the code is kept
    from app.application.use_cases.food_broadcast import notify_lost_dishes

    a = await make_user("آرش")
    b = await make_user("بهار")
    items = _items(menu)
    clock.set(tehran(2026, 10, 19, 8, 0))
    await services.food.place_order(a, items["قیمه"].id)
    await services.food.place_guest_order(a, items["قیمه"].id, 2)
    await services.food.place_order(b, items["جوجه کباب"].id)

    rows = [r for r in VALID_ROWS if r[2] != "قیمه"] + [["1405/07/28", "سه شنبه", "کوبیده", 190000, 1]]
    parsed = parse_menu(menu_file(rows), TODAY, 100_000_000)
    result = await services.food.import_menu(menu["secretary"], parsed, "menu2.xlsx", None)
    assert set(result.lost) == {a.id}  # بهار's dish is still on the menu
    assert result.lost[a.id] == [(date(2026, 10, 20), "قیمه", 0), (date(2026, 10, 20), "قیمه", 2)]

    assert await notify_lost_dishes(services, result.menu.id, result.lost) == 1
    text = messenger.texts_to(a.chat_id)[-1]
    assert "در منوی جدید نیست" in text and "قیمه (سفارش شما)" in text and "قیمه (مهمان × ۲)" in text
    assert not messenger.texts_to(b.chat_id)
    assert await notify_lost_dishes(services, result.menu.id, result.lost) == 0  # never twice
