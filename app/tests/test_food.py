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
    secretary = await make_user("منشی", RoleCode.ADMIN_1_SECRETARY)
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
