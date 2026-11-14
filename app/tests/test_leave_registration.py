"""An admin registers a leave; the employee's Excel row changes exactly as the organization defined."""

import io
import shutil
from datetime import date, time

import openpyxl
import pytest

from app.application.use_cases.register_leave import LeaveInput, plan_leave, register_leave
from app.core.exceptions import ConflictError, NotFoundError, PermissionDeniedError, ValidationError
from app.core.security.rbac import RoleCode
from app.domain.leave.excel_rules import DayTimes, LeaveRuleError, daily_leave_cells, hourly_leave_cells
from app.domain.leave.models import LeavePayType, LeaveUnit
from app.infrastructure.excel.timesheet import TEMPLATE_PATH
from app.infrastructure.excel.workbook import WorkbookStore

ORG = DayTimes(start=8 * 60, float_end=9 * 60 + 30, allowed_exit=16 * 60 + 48)  # 08:00, 09:30, 16:48
E, F, G, I_MID, J_EDGE = "شروع شناوری", "پایان شناوری", "ساعت مجاز خروج", "مرخصی وسط روز", "مرخصي اول و آخر"


def m(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


# --- the three cases, exactly as defined -------------------------------------------------------------------


def test_case_1_start_of_the_day() -> None:
    leave = hourly_leave_cells(m("08:00"), m("10:30"), ORG)
    assert leave.scenario == 1
    assert leave.cells == {E: m("10:30"), F: m("10:30"), J_EDGE: m("2:30")}  # E = 08:00 + (y − x)
    at_the_limit = hourly_leave_cells(m("09:30"), m("11:00"), ORG)  # x = 09:30 is still case 1
    assert at_the_limit.scenario == 1 and at_the_limit.cells == {E: m("09:30"), F: m("11:00"), J_EDGE: m("1:30")}


def test_case_2_middle_of_the_day_changes_only_column_i() -> None:
    leave = hourly_leave_cells(m("09:31"), m("12:00"), ORG)
    assert leave.scenario == 2 and leave.cells == {I_MID: m("2:29")}
    assert hourly_leave_cells(m("13:00"), m("16:47"), ORG).cells == {I_MID: m("3:47")}  # y < 16:48


def test_case_3_end_of_the_day() -> None:
    leave = hourly_leave_cells(m("13:00"), m("16:48"), ORG)
    assert leave.scenario == 3 and leave.cells == {G: m("13:00"), J_EDGE: m("3:48")}


@pytest.mark.parametrize(
    ("x", "y", "reason"),
    [
        ("09:00", "16:48", "هیچ‌یک"),  # x <= 09:30 and y = 16:48: no case
        ("08:00", "16:48", "هیچ‌یک"),
        ("10:00", "17:00", "بعد از"),  # y after 16:48
        ("11:00", "11:00", "قبل از ساعت پایان"),  # x = y
        ("12:00", "10:00", "قبل از ساعت پایان"),  # x > y
        ("07:30", "09:00", "شروع ساعت کاری"),  # x before 08:00
    ],
)
def test_anything_outside_the_three_cases_is_refused(x: str, y: str, reason: str) -> None:
    with pytest.raises(LeaveRuleError, match=reason):
        hourly_leave_cells(m(x), m(y), ORG)


def test_daily_leave_is_the_whole_official_day() -> None:
    assert daily_leave_cells(ORG).cells == {J_EDGE: m("8:48")}


def test_the_reference_times_come_from_the_persons_row() -> None:
    form_b = DayTimes(8 * 60, 9 * 60 + 30, 18 * 60)  # allowed exit 18:00 in that person's file
    assert hourly_leave_cells(m("15:00"), m("18:00"), form_b).scenario == 3
    assert hourly_leave_cells(m("15:00"), m("16:48"), form_b).scenario == 2


# --- registering into the employee's Excel file ------------------------------------------------------------

MEHR_27 = date(2026, 10, 19)  # the test clock's today: دوشنبه ۲۷ مهر ۱۴۰۵, row 28 of the sheet


def org_workbook(month: str = "مهر") -> bytes:
    """The organization's sheet (the آبان template) as a month sheet of an employee's file."""
    wb = openpyxl.load_workbook(TEMPLATE_PATH)
    wb.worksheets[0].title = month
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


@pytest.fixture
def store(services, tmp_path) -> WorkbookStore:
    services.workbook_store = WorkbookStore(tmp_path)
    return services.workbook_store


async def _setup(services, make_user, content: bytes | None = None, hourly: bool = False):
    admin = await make_user("مدیر", RoleCode.SUPER_ADMIN)
    employee = await make_user("مریم کریمی")
    if hourly:
        await services.users.set_hourly(admin, employee.id, True)
    workbook = await services.workbooks.upload(admin, "maryam.xlsx", content or org_workbook())
    await services.workbooks.assign(admin, workbook.id, employee.id)
    return admin, employee, workbook


def _row(store, workbook, version: int, row: int = 28) -> dict[str, object]:
    ws = openpyxl.load_workbook(io.BytesIO(store.read(workbook.id, version))).worksheets[0]
    return {str(c.value): ws.cell(row, c.column).value for c in ws[1] if c.value}


async def test_case_1_is_written_into_the_employees_row_and_nothing_else(services, make_user, store, messenger) -> None:
    admin, employee, workbook = await _setup(services, make_user)
    data = LeaveInput(employee.id, LeavePayType.PAID, LeaveUnit.HOURLY, MEHR_27, time(8, 0), time(10, 30))
    plan = await register_leave(services, admin, data)
    assert plan.cells.scenario == 1 and plan.workbook.version == 2
    before = openpyxl.load_workbook(io.BytesIO(store.read(workbook.id, 1))).worksheets[0]
    after = openpyxl.load_workbook(io.BytesIO(store.read(workbook.id, 2))).worksheets[0]
    changed = sorted(c.coordinate for row in before.iter_rows() for c in row if c.value != after[c.coordinate].value)
    assert changed == ["E28", "F28", "J28", "Q28"]  # the three cells of the rule, and the day's note
    assert after["Q28"].value == "مرخصی ساعتی با حقوق ۰۸:۰۰ تا ۱۰:۳۰"
    assert (after["E28"].value, after["F28"].value, after["J28"].value) == (time(10, 30), time(10, 30), time(2, 30))
    assert after["K28"].value == before["K28"].value  # formulas untouched
    assert "مرخصی ساعتی با حقوق" in messenger.texts_to(employee.chat_id)[-1]


async def test_case_2_and_case_3(services, make_user, store) -> None:
    admin, employee, workbook = await _setup(services, make_user)
    mid = LeaveInput(employee.id, LeavePayType.UNPAID, LeaveUnit.HOURLY, MEHR_27, time(11, 0), time(12, 15))
    await register_leave(services, admin, mid)
    row = _row(store, workbook, 2)
    assert row[I_MID] == time(1, 15) and row[E] == time(8, 0) and row[G] == time(16, 48) and row[J_EDGE] is None
    end_of_day = LeaveInput(
        employee.id, LeavePayType.PAID, LeaveUnit.HOURLY, date(2026, 10, 18), time(14, 0), time(16, 48)
    )
    await register_leave(services, admin, end_of_day)
    row = _row(store, workbook, 3, row=27)  # 26 مهر
    assert row[G] == time(14, 0) and row[J_EDGE] == time(2, 48) and row[E] == time(8, 0)


async def test_daily_leave(services, make_user, store) -> None:
    admin, employee, workbook = await _setup(services, make_user)
    await register_leave(services, admin, LeaveInput(employee.id, LeavePayType.PAID, LeaveUnit.DAILY, MEHR_27))
    row = _row(store, workbook, 2)
    assert row[J_EDGE] == time(8, 48) and row[E] == time(8, 0)


async def test_refusals_change_nothing(services, make_user, store) -> None:
    admin, employee, workbook = await _setup(services, make_user)
    hourly = LeaveInput(employee.id, LeavePayType.PAID, LeaveUnit.HOURLY, MEHR_27, time(9, 0), time(16, 48))
    with pytest.raises(ValidationError, match="هیچ‌یک"):  # no case: nothing written
        await register_leave(services, admin, hourly)
    with pytest.raises(ValidationError, match="بین"):  # outside the leave date window
        await plan_leave(services, admin, LeaveInput(employee.id, LeavePayType.PAID, LeaveUnit.DAILY, date(2026, 1, 1)))
    await services.session.refresh(workbook)
    assert workbook.version == 1

    await register_leave(services, admin, LeaveInput(employee.id, LeavePayType.PAID, LeaveUnit.DAILY, MEHR_27))
    with pytest.raises(ConflictError, match="قبلاً مرخصی"):  # the day already has leave in the file
        await register_leave(
            services,
            admin,
            LeaveInput(employee.id, LeavePayType.PAID, LeaveUnit.HOURLY, MEHR_27, time(11, 0), time(12, 0)),
        )
    with pytest.raises(NotFoundError):
        import uuid

        await plan_leave(services, admin, LeaveInput(uuid.uuid4(), LeavePayType.PAID, LeaveUnit.DAILY, MEHR_27))


async def test_only_full_time_employees_with_a_file(services, make_user, store) -> None:
    admin, hourly_person, _ = await _setup(services, make_user, hourly=True)
    with pytest.raises(ValidationError, match="تمام‌وقت"):
        await plan_leave(services, admin, LeaveInput(hourly_person.id, LeavePayType.PAID, LeaveUnit.DAILY, MEHR_27))
    no_file = await make_user("علی رضایی")
    with pytest.raises(ValidationError, match="فایل اکسل حضور ندارد"):
        await plan_leave(services, admin, LeaveInput(no_file.id, LeavePayType.PAID, LeaveUnit.DAILY, MEHR_27))
    from app.application.use_cases.register_leave import full_time_employees

    names = [u.full_name for u in await full_time_employees(services, admin)]
    assert "علی رضایی" in names and "مریم کریمی" not in names  # the hourly person is not offered


async def test_a_file_without_the_leave_columns_is_refused(services, make_user, store) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "مهر"
    ws.append(["تاریخ", "ساعت ورود", "شروع شناوری", "پایان شناوری", "ساعت مجاز خروج", "ساعت خروج"])
    for day in range(1, 31):
        ws.append([day, None, time(8), time(9, 30), time(16, 48), None])
    buffer = io.BytesIO()
    wb.save(buffer)
    admin, employee, _ = await _setup(services, make_user, content=buffer.getvalue())
    with pytest.raises(ValidationError, match="مرخصي اول و آخر"):
        await plan_leave(services, admin, LeaveInput(employee.id, LeavePayType.PAID, LeaveUnit.DAILY, MEHR_27))


async def test_employees_cannot_register_leave(services, make_user, store) -> None:
    _, employee, _ = await _setup(services, make_user)
    with pytest.raises(PermissionDeniedError):
        await register_leave(services, employee, LeaveInput(employee.id, LeavePayType.PAID, LeaveUnit.DAILY, MEHR_27))
    secretary = await make_user("منشی", RoleCode.ADMIN_1_SECRETARY)  # every admin role may
    plan = await plan_leave(services, secretary, LeaveInput(employee.id, LeavePayType.PAID, LeaveUnit.DAILY, MEHR_27))
    assert plan.cells.scenario == 0


@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice is not installed")
async def test_the_organizations_formulas_use_the_new_cells(services, make_user, store) -> None:
    """Leave 08:00–10:30, then the person arrives 10:30 and leaves 16:48: no lateness, leave counted."""
    from app.infrastructure.excel.recalc import recalculate
    from app.infrastructure.excel.workbook import DayTimes as Punch
    from app.infrastructure.excel.workbook import read_report, write_times

    admin, employee, workbook = await _setup(services, make_user)
    await register_leave(
        services, admin, LeaveInput(employee.id, LeavePayType.PAID, LeaveUnit.HOURLY, MEHR_27, time(8), time(10, 30))
    )
    content = write_times(store.read(workbook.id, 2), 7, [Punch(27, m("10:30"), m("16:48"))])
    report = read_report(recalculate(content), 7, 27)
    day, month = dict(report.day_values), dict(report.month_values)
    assert day["تاخیر مجاز"] == "0:00" and day["تاخیر غیر مجاز"] == "0:00" and day["تعجیل"] == "0:00"
    assert day["کارکرد عادی روزانه"] == "6:18" and day[J_EDGE] == "2:30"
    assert month["مرخصی مصرفی"] == "2:30"
    assert month["کارکرد نهایی"] == "8:48"  # 6:18 worked + 2:30 leave = the full official day


# --- the form in the bot ------------------------------------------------------------------------------------


async def test_the_admin_form_from_button_to_excel(session_factory, policy, clock, tmp_path, monkeypatch) -> None:
    from app.core.config.settings import get_settings
    from app.presentation.bot.callbacks.data import AdminCb, CalCb
    from app.tests.test_bot_flows import Harness

    monkeypatch.setattr(get_settings(), "workbook_dir", tmp_path)
    bot = Harness(session_factory, policy, clock)
    admin_id, emp_id, hourly_id = 7401, 7402, 7403
    await bot.register(admin_id, "منشی اداره", RoleCode.ADMIN_1_SECRETARY, RoleCode.SUPER_ADMIN)
    await bot.register(emp_id, "مریم کریمی")
    await bot.register(hourly_id, "نیروی ساعتی")
    async with session_factory() as session:
        services = bot.services(session)
        admin = await services.users.get_by_messenger_id(admin_id)
        employee = await services.users.get_by_messenger_id(emp_id)
        hourly = await services.users.get_by_messenger_id(hourly_id)
        await services.users.set_hourly(admin, hourly.id, True)
        workbook = await services.workbooks.upload(admin, "maryam.xlsx", org_workbook())
        await services.workbooks.assign(admin, workbook.id, employee.id)

    await bot.text(admin_id, "🛠 پنل مدیریت")
    assert AdminCb(a="lr", v="0").pack() in bot.buttons(admin_id)
    await bot.press(admin_id, AdminCb(a="lr", v="0").pack())
    choices = bot.buttons(admin_id)
    assert AdminCb(a="lr_u", v=employee.id.hex).pack() in choices
    assert AdminCb(a="lr_u", v=hourly.id.hex).pack() not in choices  # full-time only
    await bot.press(admin_id, AdminCb(a="lr_u", v=employee.id.hex).pack())
    await bot.press(admin_id, AdminCb(a="lr_t", v="PAID").pack())
    await bot.press(admin_id, AdminCb(a="lr_n", v="HOURLY").pack())
    await bot.press(admin_id, CalCb(p="lr_d", a="d", v=MEHR_27.toordinal()).pack())
    assert "ساعت شروع" in bot.last_text(admin_id)
    await bot.text(admin_id, "۰۸:۰۰")
    await bot.text(admin_id, "10:30")
    preview = bot.last_text(admin_id)
    assert "مرخصی اول روز" in preview and "شروع شناوری: ۱۰:۳۰" in preview and "مرخصي اول و آخر: ۲:۳۰" in preview
    await bot.press(admin_id, AdminCb(a="lr_ok").pack())
    assert "در فایل اکسل ثبت شد" in bot.last_text(admin_id)
    assert "مرخصی ساعتی با حقوق" in bot.last_text(emp_id)  # the employee is told
    await bot.press(admin_id, AdminCb(a="lr_ok").pack())  # an old button: nothing happens twice
    async with session_factory() as session:
        services = bot.services(session)
        services.workbook_store = WorkbookStore(tmp_path)
        current = await services.workbooks.for_user(employee.id)
        assert current.version == 2
        sheet = openpyxl.load_workbook(io.BytesIO(services.workbook_store.read(current.id, 2))).worksheets[0]
        assert (sheet["E28"].value, sheet["F28"].value, sheet["J28"].value) == (time(10, 30), time(10, 30), time(2, 30))

    # A case that fits no rule: a clear error and the file is not touched
    await bot.press(admin_id, AdminCb(a="lr", v="0").pack())
    await bot.press(admin_id, AdminCb(a="lr_u", v=employee.id.hex).pack())
    await bot.press(admin_id, AdminCb(a="lr_t", v="UNPAID").pack())
    await bot.press(admin_id, AdminCb(a="lr_n", v="HOURLY").pack())
    await bot.press(admin_id, CalCb(p="lr_d", a="d", v=date(2026, 10, 18).toordinal()).pack())
    await bot.text(admin_id, "09:00")
    await bot.text(admin_id, "16:48")
    assert "هیچ‌یک" in bot.last_text(admin_id) and "چیزی در فایل اکسل تغییر نکرد" in bot.last_text(admin_id)

    # Employees have no button and a forged press is refused
    await bot.press(emp_id, AdminCb(a="lr", v="0").pack())
    assert "اجازه" in bot.last_text(emp_id)


async def test_the_note_is_added_after_an_existing_one(services, make_user, store) -> None:
    content = org_workbook()
    wb = openpyxl.load_workbook(io.BytesIO(content))
    wb.worksheets[0]["Q28"] = "تعمیر کلید"
    buffer = io.BytesIO()
    wb.save(buffer)
    admin, employee, workbook = await _setup(services, make_user, content=buffer.getvalue())
    await register_leave(services, admin, LeaveInput(employee.id, LeavePayType.UNPAID, LeaveUnit.DAILY, MEHR_27))
    assert _row(store, workbook, 2)["توضیحات"] == "تعمیر کلید | مرخصی روزانه بدون حقوق"


async def test_the_leave_shows_in_every_excel_export_of_that_person(services, make_user, store, clock) -> None:
    """The exported timesheet takes E/F/G (where changed), I, J and the notes from the person's file;
    clearing them in the file and uploading it again removes them from the export too."""
    from app.application.use_cases.workbook_report import workbook_file_rows
    from app.infrastructure.excel.timesheet import build_timesheet_workbook

    admin, employee, workbook = await _setup(services, make_user)
    await register_leave(
        services, admin, LeaveInput(employee.id, LeavePayType.PAID, LeaveUnit.HOURLY, MEHR_27, time(8), time(10, 30))
    )
    report = await services.attendance.my_month(employee)

    async def export_row() -> dict:
        rows = await workbook_file_rows(services, [employee.id], report.start, report.end)
        content = build_timesheet_workbook(
            [report], services.attendance.sheet_rules, services.attendance.holidays, file_rows=rows
        )
        ws = openpyxl.load_workbook(io.BytesIO(content)).worksheets[0]
        return {str(c.value): ws.cell(28, c.column).value for c in ws[1] if c.value}

    row = await export_row()
    assert (row[E], row[F], row[G]) == (time(10, 30), time(10, 30), time(16, 48))
    assert row[J_EDGE] == time(2, 30) and row[I_MID] is None
    assert "مرخصی ساعتی با حقوق ۰۸:۰۰ تا ۱۰:۳۰" in row["توضیحات"]

    # Cancelled in Excel: the admin uploads the file with the cells cleared
    wb = openpyxl.load_workbook(io.BytesIO(store.read(workbook.id, 2)))
    for ref in ("E28", "F28"):
        wb.worksheets[0][ref] = time(8) if ref == "E28" else time(9, 30)
    wb.worksheets[0]["J28"] = None
    wb.worksheets[0]["Q28"] = None
    buffer = io.BytesIO()
    wb.save(buffer)
    cleared = await services.workbooks.upload(admin, "maryam-cleared.xlsx", buffer.getvalue())
    await services.workbooks.assign(admin, cleared.id, employee.id)
    row = await export_row()
    assert (row[E], row[F], row[J_EDGE]) == (time(8), time(9, 30), None)
    assert not row["توضیحات"] or "مرخصی" not in row["توضیحات"]


async def test_people_without_their_own_file_export_as_before(services, make_user, store, clock) -> None:
    from app.application.use_cases.workbook_report import workbook_file_rows

    employee = await make_user("بدون فایل")
    report = await services.attendance.my_month(employee)
    assert await workbook_file_rows(services, [employee.id], report.start, report.end) == {}
