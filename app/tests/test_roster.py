"""Staff list: number normalization, the Excel upload and replacing the list."""

import io

import pytest
from openpyxl import Workbook

from app.core.exceptions import ConflictError, NotFoundError
from app.core.security.rbac import RoleCode
from app.core.utils.text import normalize_employee_code
from app.infrastructure.excel.roster import build_roster_workbook, parse_roster
from app.tests.conftest import add_roster


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("09121234567", "09121234567"),
        ("۰۹۱۲۱۲۳۴۵۶۷", "09121234567"),
        ("+98 912 123 4567", "09121234567"),
        ("00989121234567", "09121234567"),
        ("9121234567", "09121234567"),
        ("0912-123-4567", "09121234567"),
        ("9121234567.0", "09121234567"),  # a number cell read as a float
        ("1402", "1402"),  # a personnel code stays as it is
        ("abc", None),
        ("12", None),
    ],
)
def test_normalize_employee_code(typed: str, expected: str | None) -> None:
    assert normalize_employee_code(typed) == expected


def _xlsx(rows: list[list[object]]) -> bytes:
    wb = Workbook()
    for row in rows:
        wb.active.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def test_parse_the_template_round_trip() -> None:
    content = build_roster_workbook([("09121234567", "زهرا احمدی", True, "زهرا احمدی"), ("1402", None, False, None)])
    parsed = parse_roster(content)
    assert parsed.ok
    assert [(r.code, r.full_name, r.hourly) for r in parsed.rows] == [
        ("09121234567", "زهرا احمدی", True),
        ("1402", None, False),
    ]


def test_parse_without_header_and_with_number_cells() -> None:
    parsed = parse_roster(_xlsx([[9121234567, "علی رضایی", "ساعتی"], ["۰۹۳۵۱۱۱۲۲۳۳", "مریم کریمی"]]))
    assert parsed.ok and [r.code for r in parsed.rows] == ["09121234567", "09351112233"]
    assert parsed.rows[0].hourly and not parsed.rows[1].hourly


def test_parse_reports_bad_and_duplicate_rows() -> None:
    parsed = parse_roster(_xlsx([["شماره", "نام"], ["0912abc", "x"], ["09121234567", "a"], ["+989121234567", "b"]]))
    assert not parsed.ok
    assert any("ردیف ۲" in e and "معتبر" in e for e in parsed.errors)
    assert any("ردیف ۴" in e and "تکراری" in e for e in parsed.errors)
    assert not parse_roster(b"not an excel file").ok


async def test_replacing_the_list(services, make_user) -> None:
    admin = await make_user("مدیر", RoleCode.SUPER_ADMIN)
    await add_roster(services, "09120000001", "قدیمی")
    await add_roster(services, "09120000002", "تغییر نام")
    used = await make_user("ثبت‌نام‌کرده")  # registered with its own number (not in the new file)
    rows = [("09120000002", "نام جدید", True), ("09120000003", None, False)]
    preview = await services.users.import_roster(admin, rows, dry_run=True)
    assert (preview.added, preview.updated, preview.removed) == (1, 1, 1)
    assert preview.kept_registered == 2  # the admin's and the other user's own numbers
    assert len(await services.users.roster_entries(admin)) == 4  # dry run changed nothing
    await services.users.import_roster(admin, rows)
    codes = {e.code: e for e, _ in await services.users.roster_entries(admin)}
    assert "09120000001" not in codes and "09120000003" in codes
    assert codes["09120000002"].full_name == "نام جدید" and codes["09120000002"].hourly
    assert await services.users.roster_code_of(used) in codes  # registered numbers are kept


async def test_claiming_and_hourly_from_the_list(services, make_user) -> None:
    admin = await make_user("مدیر", RoleCode.SUPER_ADMIN)
    await services.users.import_roster(admin, [("09125550000", "نیروی ساعتی", True)])
    with pytest.raises(NotFoundError):
        await services.users.find_roster_entry("09125550001")
    user = await services.users.register(42, 42, None, None, roster_code="09125550000")
    assert user.full_name == "نیروی ساعتی" and user.is_hourly
    with pytest.raises(ConflictError):
        await services.users.find_roster_entry("09125550000")
    # Changing the type on the list changes the registered account too.
    await services.users.import_roster(admin, [("09125550000", "نیروی ساعتی", False)])
    await services.session.refresh(user)
    assert not user.is_hourly


async def test_people_registered_before_the_list_are_linked_by_name(services, make_user, policy) -> None:
    admin = await make_user("مدیر", RoleCode.SUPER_ADMIN)
    policy.users.require_employee_roster = False
    early = await services.users.register(55, 55, "علی رضايي", None)  # Arabic ي, before the list existed
    policy.users.require_employee_roster = True
    rows = [("09127770000", "علی  رضایی", True)]
    preview = await services.users.import_roster(admin, rows, dry_run=True)
    assert preview.linked == 1
    await services.users.import_roster(admin, rows)
    assert await services.users.roster_code_of(early) == "09127770000"
    await services.session.refresh(early)
    assert early.is_hourly
    with pytest.raises(ConflictError):  # nobody else can take the number now
        await services.users.find_roster_entry("09127770000")
