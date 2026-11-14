"""Admin-uploaded attendance workbooks: the bot writes check-in/out, Excel's formulas calculate the rest."""

import io
import shutil
from datetime import date, time

import openpyxl
import pytest
from openpyxl.comments import Comment

from app.application.dto.attendance import CorrectionInput
from app.application.use_cases.workbook_report import update_workbook
from app.core.exceptions import PermissionDeniedError, ValidationError
from app.core.security.rbac import RoleCode
from app.core.utils.jalali import to_gregorian
from app.domain.attendance.models import CorrectionType
from app.infrastructure.excel.recalc import RecalculationError
from app.infrastructure.excel.workbook import (
    DayTimes,
    WorkbookError,
    WorkbookStore,
    _minutes,
    inspect_workbook,
    read_report,
    write_times,
)
from app.tests.conftest import tehran

needs_libreoffice = pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice is not installed")

AZAR = 9  # آذر


def sample_workbook(months=("آبان", "آذر"), broken_formula: bool = False) -> bytes:
    """A small timesheet in the organization's style: inputs, formulas per day, labelled totals."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for month in months:
        ws = wb.create_sheet(month)
        headers = ["روز", "ماه", "تاریخ", "ساعت ورود", "پایان شناوری", "ساعت خروج", "ساعت مفید", "تاخیر غیر مجاز"]
        ws.append(headers)
        for day in range(1, 31):
            r = day + 1
            ws.append(["", month, day, None, time(9, 30), None, f"=F{r}-D{r}", f"=IF(D{r}>E{r},D{r}-E{r},0)"])
            for col in "DEFGH":
                ws[f"{col}{r}"].number_format = "h:mm"
        ws["A33"], ws["B33"] = "مجموع ساعت مفید:", "=SUM(G2:G31)"
        ws["A34"], ws["B34"] = "مجموع تاخیر غیرمجاز:", "=SUM(H2:H31)"
        ws["A35"], ws["B35"] = "کارکرد نهایی:", "=B33-B34" if not broken_formula else "=B33/0"
        for ref in ("B33", "B34", "B35"):
            ws[ref].number_format = "[h]:mm"
        ws["A33"].comment = Comment("comment written by the admin", "admin")
    wb.create_sheet("راهنما")["A1"] = "not a month sheet"
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def test_inspect_finds_month_sheets_by_name_and_headers() -> None:
    assert inspect_workbook(sample_workbook()).sheets == ["آبان", "آذر"]


def test_inspect_rejects_files_without_the_required_columns() -> None:
    wb = openpyxl.Workbook()
    wb.active.title = "آذر"
    wb.active.append(["تاریخ", "ورود", "خروج"])
    buffer = io.BytesIO()
    wb.save(buffer)
    with pytest.raises(WorkbookError, match="ساعت ورود"):
        inspect_workbook(buffer.getvalue())
    with pytest.raises(WorkbookError, match="xlsx"):
        inspect_workbook(b"not an excel file")


def test_write_changes_only_the_check_in_and_check_out_cells() -> None:
    original = sample_workbook()
    written = write_times(original, AZAR, [DayTimes(17, 9 * 60 + 40, 17 * 60 + 5), DayTimes(18, 8 * 60, None)])
    before, after = openpyxl.load_workbook(io.BytesIO(original)), openpyxl.load_workbook(io.BytesIO(written))
    changed = [
        (ws.title, c.coordinate)
        for ws in before.worksheets
        for row in ws.iter_rows()
        for c in row
        if c.value != after[ws.title][c.coordinate].value
    ]
    assert sorted(changed) == [("آذر", "D18"), ("آذر", "D19"), ("آذر", "F18")]
    sheet = after["آذر"]
    assert sheet["D18"].value == time(9, 40) and sheet["F18"].value == time(17, 5)
    assert sheet["G18"].value == "=F18-D18"  # the admin's formula is untouched
    assert sheet["A33"].comment.text == "comment written by the admin"
    assert sheet["F19"].value is None  # a missing check-out leaves the cell empty


def test_check_out_after_midnight_is_written_as_more_than_a_day() -> None:
    written = write_times(sample_workbook(), AZAR, [DayTimes(17, 22 * 60, 26 * 60)])
    cell = openpyxl.load_workbook(io.BytesIO(written))["آذر"]["F18"]
    assert _minutes(cell.value, cell.number_format) == 26 * 60  # 02:00 the next day


def test_write_refuses_a_missing_month_or_a_formula_in_an_input_cell() -> None:
    with pytest.raises(WorkbookError, match="دی"):
        write_times(sample_workbook(), 10, [DayTimes(1, 480, None)])
    wb = openpyxl.load_workbook(io.BytesIO(sample_workbook()))
    wb["آذر"]["D2"] = "=E2"
    buffer = io.BytesIO()
    wb.save(buffer)
    with pytest.raises(WorkbookError, match="فرمول"):
        write_times(buffer.getvalue(), AZAR, [DayTimes(1, 480, None)])


def test_store_keeps_versions_and_writes_atomically(tmp_path) -> None:
    import uuid

    store = WorkbookStore(tmp_path, keep_versions=2)
    wid = uuid.uuid4()
    for version in (1, 2, 3):
        store.save(wid, version, f"v{version}".encode())
    assert store.read(wid, 3) == b"v3"
    assert sorted(p.name for p in (tmp_path / wid.hex).iterdir()) == ["v0002.xlsx", "v0003.xlsx"]
    with pytest.raises(WorkbookError):
        store.read(wid, 1)


@needs_libreoffice
def test_libreoffice_calculates_and_the_report_is_read_by_labels() -> None:
    from app.infrastructure.excel.recalc import recalculate

    times = DayTimes(17, 9 * 60 + 40, 17 * 60 + 5)
    report = read_report(recalculate(write_times(sample_workbook(), AZAR, [times])), AZAR, 17, times)
    assert report.ok, report.errors
    assert dict(report.day_values) == {
        "ساعت ورود": "9:40",
        "پایان شناوری": "9:30",
        "ساعت خروج": "17:05",
        "ساعت مفید": "7:25",
        "تاخیر غیر مجاز": "0:10",
    }
    assert dict(report.month_values) == {
        "مجموع ساعت مفید": "7:25",
        "مجموع تاخیر غیرمجاز": "0:10",
        "کارکرد نهایی": "7:15",
    }


@needs_libreoffice
def test_formula_errors_and_values_that_do_not_match_are_reported() -> None:
    from app.infrastructure.excel.recalc import recalculate

    times = DayTimes(17, 9 * 60 + 40, 17 * 60 + 5)
    calculated = recalculate(write_times(sample_workbook(broken_formula=True), AZAR, [times]))
    report = read_report(calculated, AZAR, 17, times)
    assert not report.ok and any("کارکرد نهایی" in e for e in report.errors)
    other = read_report(calculated, AZAR, 17, DayTimes(17, 8 * 60, None))  # not what was written
    assert any("ساعت ورود" in e for e in other.errors)


# --- service and the write → calculate → report flow ----------------------------------------------------


@pytest.fixture
def store(services, tmp_path) -> WorkbookStore:
    services.workbook_store = WorkbookStore(tmp_path)
    return services.workbook_store


async def test_only_super_admins_upload_and_assign(services, make_user, store) -> None:
    employee = await make_user("کارمند")
    with pytest.raises(PermissionDeniedError):
        await services.workbooks.upload(employee, "x.xlsx", sample_workbook())
    secretary = await make_user("منشی", RoleCode.ADMIN_1_SECRETARY)
    with pytest.raises(PermissionDeniedError):
        await services.workbooks.upload(secretary, "x.xlsx", sample_workbook())
    admin = await make_user("مدیر", RoleCode.SUPER_ADMIN)
    with pytest.raises(ValidationError):
        await services.workbooks.upload(admin, "bad.xlsx", b"nope")
    first = await services.workbooks.upload(admin, "a.xlsx", sample_workbook())
    second = await services.workbooks.upload(admin, "b.xlsx", sample_workbook())
    assert first.sheets == "آبان،آذر" and store.read(first.id, 1)
    await services.workbooks.assign(admin, first.id, employee.id)
    assert (await services.workbooks.for_user(employee.id)).id == first.id
    await services.workbooks.assign(admin, second.id, employee.id)  # one file per person: replaces
    assert (await services.workbooks.for_user(employee.id)).id == second.id
    assert (await services.workbooks.get(admin, first.id)).user_id is None
    await services.workbooks.assign(admin, None, employee.id)
    assert await services.workbooks.for_user(employee.id) is None


async def _punch(services, user, clock, day: int, check_in: time, check_out: time | None) -> date:
    work_date = to_gregorian(1405, AZAR, day)
    clock.set(tehran(work_date.year, work_date.month, work_date.day, 23, 0))
    kind = CorrectionType.BOTH if check_out else CorrectionType.CHECK_IN
    await services.attendance.submit_correction(
        user, CorrectionInput(kind, work_date, check_in, check_out, same_day=True)
    )
    return work_date


@needs_libreoffice
async def test_check_in_and_out_go_into_the_file_and_excel_results_are_sent(
    services, make_user, store, clock, messenger
) -> None:
    admin = await make_user("مدیر", RoleCode.SUPER_ADMIN)
    employee = await make_user("مریم کریمی")
    workbook = await services.workbooks.upload(admin, "maryam.xlsx", sample_workbook())
    await services.workbooks.assign(admin, workbook.id, employee.id)
    work_date = await _punch(services, employee, clock, 17, time(9, 40), time(17, 5))

    run = await update_workbook(services, employee.id, work_date)
    assert run.status == "sent", run.error
    text = messenger.texts_to(employee.chat_id)[-1]
    for line in ("گزارش حضور — مریم کریمی", "ساعت مفید: ۷:۲۵", "تاخیر غیر مجاز: ۰:۱۰", "کارکرد نهایی: ۷:۱۵"):
        assert line in text, line
    # A new version holds the written times; the uploaded original is kept.
    await services.session.refresh(workbook)
    assert workbook.version == 2
    stored = openpyxl.load_workbook(io.BytesIO(store.read(workbook.id, 2)))["آذر"]
    assert stored["D18"].value == time(9, 40) and stored["F18"].value == time(17, 5)
    assert openpyxl.load_workbook(io.BytesIO(store.read(workbook.id, 1)))["آذر"]["D18"].value is None


async def test_people_without_a_workbook_are_left_alone(services, make_user, store, messenger) -> None:
    employee = await make_user()
    run = await update_workbook(services, employee.id, date(2026, 12, 8))
    assert run.status == "no_workbook" and messenger.sent == []


async def test_a_month_missing_from_the_file_sends_no_figures(services, make_user, store, clock, messenger) -> None:
    admin = await make_user("مدیر", RoleCode.SUPER_ADMIN)
    employee = await make_user("کارمند")
    workbook = await services.workbooks.upload(admin, "only-aban.xlsx", sample_workbook(months=("آبان",)))
    await services.workbooks.assign(admin, workbook.id, employee.id)
    work_date = await _punch(services, employee, clock, 17, time(9, 40), time(17, 5))
    run = await update_workbook(services, employee.id, work_date)
    assert run.status == "failed" and "آذر" in run.error
    assert "آماده نشد" in messenger.texts_to(employee.chat_id)[-1]
    assert "آذر" in messenger.texts_to(admin.chat_id)[-1]
    await services.session.refresh(workbook)
    assert workbook.version == 1  # nothing written


async def test_recalculation_failure_keeps_the_written_times_but_sends_no_figures(
    services, make_user, store, clock, messenger, monkeypatch
) -> None:
    import app.application.use_cases.workbook_report as flow

    def broken(_content: bytes) -> bytes:
        raise RecalculationError("soffice crashed")

    monkeypatch.setattr(flow, "recalculate", broken)
    admin = await make_user("مدیر", RoleCode.SUPER_ADMIN)
    employee = await make_user("کارمند")
    workbook = await services.workbooks.upload(admin, "a.xlsx", sample_workbook())
    await services.workbooks.assign(admin, workbook.id, employee.id)
    work_date = await _punch(services, employee, clock, 17, time(9, 40), None)
    run = await update_workbook(services, employee.id, work_date)
    assert run.status == "failed"
    assert "ساعت" not in messenger.texts_to(employee.chat_id)[-1].split("\n")[0]
    assert "LibreOffice" in messenger.texts_to(admin.chat_id)[-1]
    await services.session.refresh(workbook)
    assert workbook.version == 2  # the check-in is in the file for when it works again


async def test_assigning_a_file_writes_the_whole_month_without_messaging(
    services, make_user, store, clock, messenger
) -> None:
    admin = await make_user("مدیر", RoleCode.SUPER_ADMIN)
    employee = await make_user("کارمند")
    await _punch(services, employee, clock, 3, time(8, 10), time(16, 50))
    work_date = await _punch(services, employee, clock, 4, time(8, 20), time(17, 0))
    workbook = await services.workbooks.upload(admin, "a.xlsx", sample_workbook())
    await services.workbooks.assign(admin, workbook.id, employee.id)
    sent_before = len(messenger.sent)
    run = await update_workbook(services, employee.id, work_date, sync_month=True, send_report=False)
    assert run.status == "written" and len(messenger.sent) == sent_before
    await services.session.refresh(workbook)
    sheet = openpyxl.load_workbook(io.BytesIO(store.read(workbook.id, workbook.version)))["آذر"]
    assert (sheet["D4"].value, sheet["F4"].value, sheet["D5"].value) == (time(8, 10), time(16, 50), time(8, 20))


# --- through the bot: admin buttons and the employee's check-in --------------------------------------------


async def test_admin_assigns_a_file_and_check_in_replies_with_the_time_only(
    session_factory, policy, clock, tmp_path, monkeypatch
) -> None:
    from app.core.config.settings import get_settings
    from app.infrastructure.tasks import dispatch
    from app.presentation.bot.callbacks.data import AdminCb, AttCb, UserAdminCb
    from app.tests.test_bot_flows import Harness

    monkeypatch.setattr(get_settings(), "workbook_dir", tmp_path)
    queued: list[tuple] = []
    monkeypatch.setattr(dispatch, "enqueue_workbook_update", lambda *args, **kw: queued.append((args, kw)))
    bot = Harness(session_factory, policy, clock)
    admin_id, emp_id = 7001, 7002
    await bot.register(admin_id, "مدیر سیستم", RoleCode.SUPER_ADMIN)
    await bot.register(emp_id, "مریم کریمی")
    async with session_factory() as session:
        services = bot.services(session)
        admin = await services.users.get_by_messenger_id(admin_id)
        employee = await services.users.get_by_messenger_id(emp_id)
        workbook = await services.workbooks.upload(admin, "maryam.xlsx", sample_workbook())

    # Admin panel → the files screen lists it; the employee card offers it.
    await bot.text(admin_id, "🛠 پنل مدیریت")
    assert AdminCb(a="wb").pack() in bot.buttons(admin_id)
    await bot.press(admin_id, AdminCb(a="wb").pack())
    assert AdminCb(a="wb_v", v=workbook.id.hex).pack() in bot.buttons(admin_id)
    await bot.press(admin_id, UserAdminCb(a="view", u=employee.id.hex).pack())
    assert UserAdminCb(a="wb", u=employee.id.hex).pack() in bot.buttons(admin_id)
    await bot.press(admin_id, UserAdminCb(a="wb", u=employee.id.hex).pack())
    await bot.press(admin_id, AdminCb(a="wb_set", v=workbook.id.hex).pack())
    assert "maryam.xlsx" in bot.last_text(admin_id) and "داده شد" in bot.last_text(admin_id)
    assert queued[-1] == ((employee.id, date(2026, 10, 19)), {"sync_month": True})

    # The employee's check-in: the time only, and the Excel job is queued for that day.
    clock.set(tehran(2026, 10, 19, 9, 3))
    await bot.press(emp_id, AttCb(a="in").pack())
    confirm = next(b for b in bot.buttons(emp_id) if b.startswith("att:in_ok"))
    await bot.press(emp_id, confirm)
    reply = bot.last_text(emp_id)
    assert "ورود شما ثبت شد" in reply and "فایل اکسل" in reply
    assert "تأخیر" not in reply  # no figures calculated by the bot
    assert queued[-1] == ((employee.id, date(2026, 10, 19)), {})

    # Someone without a file keeps the bot's own reply.
    other_id = 7003
    await bot.register(other_id, "علی رضایی")
    await bot.press(other_id, AttCb(a="in").pack())
    await bot.press(other_id, next(b for b in bot.buttons(other_id) if b.startswith("att:in_ok")))
    assert "تأخیر" in bot.last_text(other_id) and "فایل اکسل" not in bot.last_text(other_id)
    assert len(queued) == 2


async def test_the_files_screen_is_for_super_admins_only(session_factory, policy, clock, tmp_path, monkeypatch) -> None:
    from app.core.config.settings import get_settings
    from app.presentation.bot.callbacks.data import AdminCb
    from app.tests.test_bot_flows import Harness

    monkeypatch.setattr(get_settings(), "workbook_dir", tmp_path)
    bot = Harness(session_factory, policy, clock)
    await bot.register(7101, "منشی", RoleCode.ADMIN_1_SECRETARY)
    await bot.text(7101, "🛠 پنل مدیریت")
    assert AdminCb(a="wb").pack() not in bot.buttons(7101)
    await bot.press(7101, AdminCb(a="wb").pack())  # a forged button press is refused
    assert "اجازه" in bot.last_text(7101)


@needs_libreoffice
async def test_a_report_that_telegram_refuses_is_not_counted_as_sent(
    services, make_user, store, clock, messenger
) -> None:
    admin = await make_user("مدیر", RoleCode.SUPER_ADMIN)
    employee = await make_user("کارمند")
    workbook = await services.workbooks.upload(admin, "a.xlsx", sample_workbook())
    await services.workbooks.assign(admin, workbook.id, employee.id)
    work_date = await _punch(services, employee, clock, 17, time(9, 40), time(17, 5))
    messenger.fail_chats.add(employee.chat_id)  # e.g. the employee blocked the bot
    run = await update_workbook(services, employee.id, work_date)
    assert run.status == "not_delivered" and run.report is not None and run.report.ok


# --- weekly and monthly reports from the Excel file -----------------------------------------------------


def test_only_the_configured_month_totals_are_shown_in_order() -> None:
    from app.application.presenters.workbook import pick_totals

    values = [("کارکرد نهایی", "5:12"), ("مجموع تعجیل", "0:00"), ("مجموع اضافه کار", "4:54")]
    assert pick_totals(values, ["مجموع اضافه کار", "مجموع تعجیل:"]) == [
        ("مجموع اضافه کار", "4:54"),
        ("مجموع تعجیل", "0:00"),
    ]
    assert pick_totals(values, []) == values  # nothing configured: everything
    assert pick_totals(values, ["برچسبی که در فایل نیست"]) == values  # another form: everything, not nothing


@needs_libreoffice
def test_period_report_reads_the_days_and_the_month_totals() -> None:
    from app.application.presenters.workbook import period_text
    from app.infrastructure.excel.recalc import recalculate
    from app.infrastructure.excel.workbook import read_period

    source = write_times(sample_workbook(), AZAR, [DayTimes(3, 9 * 60 + 40, 17 * 60 + 5), DayTimes(4, 8 * 60, None)])
    report = read_period(source, recalculate(source), [(AZAR, 3), (AZAR, 4), (AZAR, 5), (10, 1)], AZAR)
    assert report.ok, report.errors
    days = {d.day: d for d in report.days}
    assert (days[3].check_in, days[3].check_out) == ("9:40", "17:05")
    assert dict(days[3].values) == {"ساعت مفید": "7:25", "تاخیر غیر مجاز": "0:10"}  # formula columns only
    assert days[5].check_in is None and 1 not in days  # دی has no sheet: left out
    dates = [to_gregorian(1405, AZAR, d) for d in (3, 4, 5)]
    text = period_text("📊 گزارش هفتگی من", "مریم", dates[0], dates[-1], report, dates, ["کارکرد نهایی"])
    assert "ورود ۹:۴۰ | خروج ۱۷:۰۵" in text and "ساعت مفید ۷:۲۵" in text
    assert "بدون ثبت ورود و خروج" in text  # day 5
    # Day 4 has no check-out yet: the formula exit − entry gives −8:00, and Excel's total includes it.
    assert "ورود ۸:۰۰ | خروج —" in text and "ساعت مفید -۸:۰۰" in text
    assert "▪️ کارکرد نهایی: -۰:۴۵" in text and "مجموع ساعت مفید" not in text  # only the configured total
    assert "یکشنبه یکشنبه" not in text


async def test_weekly_and_monthly_buttons_read_the_excel_file_for_people_who_have_one(
    session_factory, policy, clock, tmp_path, monkeypatch
) -> None:
    from app.core.config.settings import get_settings
    from app.infrastructure.tasks import dispatch
    from app.presentation.bot.callbacks.data import AttCb
    from app.tests.test_bot_flows import Harness

    if shutil.which("soffice") is None:
        pytest.skip("LibreOffice is not installed")
    monkeypatch.setattr(get_settings(), "workbook_dir", tmp_path)
    monkeypatch.setattr(dispatch, "enqueue_workbook_update", lambda *a, **k: None)
    policy.attendance.workbook_report_labels = ["مجموع ساعت مفید", "کارکرد نهایی"]
    bot = Harness(session_factory, policy, clock)
    await bot.register(7201, "مدیر سیستم", RoleCode.SUPER_ADMIN)
    await bot.register(7202, "مریم کریمی")
    await bot.register(7203, "علی رضایی")  # no Excel file
    async with session_factory() as session:
        services = bot.services(session)
        admin = await services.users.get_by_messenger_id(7201)
        employee = await services.users.get_by_messenger_id(7202)
        # 27 مهر (the clock's today) has a check-in and check-out in the file
        content = write_times(sample_workbook(months=("مهر",)), 7, [DayTimes(27, 9 * 60, 17 * 60)])
        workbook = await services.workbooks.upload(admin, "maryam.xlsx", content)
        await services.workbooks.assign(admin, workbook.id, employee.id)

    clock.set(tehran(2026, 10, 19, 18, 0))
    await bot.press(7202, AttCb(a="week").pack())
    week = bot.last_text(7202)
    assert "(از فایل اکسل)" in week and "ورود ۹:۰۰ | خروج ۱۷:۰۰" in week and "ساعت مفید ۸:۰۰" in week
    assert "▪️ مجموع ساعت مفید: ۸:۰۰" in week and "▪️ کارکرد نهایی: ۸:۰۰" in week
    await bot.press(7202, AttCb(a="month").pack())
    month = bot.last_text(7202)
    assert "گزارش ماهانه من (از فایل اکسل)" in month and "▪️ کارکرد نهایی: ۸:۰۰" in month
    assert "▫️" not in month  # monthly: totals only

    await bot.press(7203, AttCb(a="week").pack())  # without a file: the bot's own report, as before
    assert "گزارش هفتگی من" in bot.last_text(7203) and "از فایل اکسل" not in bot.last_text(7203)


async def test_a_broken_file_gives_a_clear_message_and_alerts_the_admin(
    session_factory, policy, clock, tmp_path, monkeypatch
) -> None:
    import app.application.use_cases.workbook_report as flow
    from app.core.config.settings import get_settings
    from app.presentation.bot.callbacks.data import AttCb
    from app.tests.test_bot_flows import Harness

    def broken(_content: bytes) -> bytes:
        raise RecalculationError("soffice crashed")

    monkeypatch.setattr(get_settings(), "workbook_dir", tmp_path)
    monkeypatch.setattr(flow, "recalculate", broken)
    bot = Harness(session_factory, policy, clock)
    await bot.register(7301, "مدیر سیستم", RoleCode.SUPER_ADMIN)
    await bot.register(7302, "مریم کریمی")
    async with session_factory() as session:
        services = bot.services(session)
        admin = await services.users.get_by_messenger_id(7301)
        employee = await services.users.get_by_messenger_id(7302)
        workbook = await services.workbooks.upload(admin, "maryam.xlsx", sample_workbook(months=("مهر",)))
        await services.workbooks.assign(admin, workbook.id, employee.id)
    await bot.press(7302, AttCb(a="month").pack())
    assert "آماده نشد" in bot.last_text(7302) and "۰:" not in bot.last_text(7302)
    assert "LibreOffice" in bot.last_text(7301)
