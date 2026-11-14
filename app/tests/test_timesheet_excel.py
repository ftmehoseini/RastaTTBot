"""Every attendance Excel file has the organization's structure: the «آبان» sheet kept as a template.

The template's formulas, recalculated by LibreOffice, must give the bot's own numbers.
"""

import asyncio
import datetime as dt
import io
import os
import shutil
import subprocess
from datetime import date, time
from pathlib import Path

import openpyxl
import pytest

from app.application.dto.attendance import CorrectionInput
from app.core.utils.jalali import month_range, to_gregorian, to_jalali
from app.domain.attendance.models import CorrectionType
from app.infrastructure.excel.timesheet import TEMPLATE_PATH, build_timesheet_workbook
from app.tests.conftest import tehran

DAYS = (
    (date(2026, 10, 17), (8, 38), (17, 6)),
    (date(2026, 10, 18), (11, 34), (19, 0)),
    (date(2026, 10, 19), (9, 25), (16, 27)),
    (date(2026, 10, 20), (7, 40), (16, 0)),
    (date(2026, 10, 22), (9, 50), (13, 20)),  # Thursday, a day off: all overtime
)
# Columns of the organization's structure
D, E, F, G, H, I_MID, J_EDGE, K_USEFUL, P_OVERTIME, Q_NOTES = 4, 5, 6, 7, 8, 9, 10, 11, 16, 17


async def _month_workbook(services, make_user, clock) -> tuple[bytes, object]:
    user = await make_user("مریم کریمی")
    clock.set(tehran(2026, 10, 22, 20, 0))  # 30 مهر, the last day of the month
    for day, check_in, check_out in DAYS:
        await services.attendance.submit_correction(
            user,
            CorrectionInput(
                CorrectionType.BOTH, day, time(*check_in), time(*check_out), same_day=day == date(2026, 10, 22)
            ),
        )
    report = await services.attendance.my_month(user)
    content = build_timesheet_workbook([report], services.attendance.sheet_rules, services.attendance.holidays)
    return content, report


def _style(cell) -> tuple:
    return (
        repr(cell.font),
        repr(cell.fill),
        repr(cell.border),
        repr(cell.alignment),
        cell.number_format,
        repr(cell.protection),
    )


async def test_the_sheet_is_the_organizations_structure_cell_for_cell(services, make_user, clock) -> None:
    """مهر has 30 days like the template (آبان): everything matches except the inputs and the table name."""
    content, _ = await _month_workbook(services, make_user, clock)
    ws = openpyxl.load_workbook(io.BytesIO(content))["مریم کریمی"]
    tpl = openpyxl.load_workbook(TEMPLATE_PATH).worksheets[0]
    table, tpl_table = next(iter(ws.tables.values())), next(iter(tpl.tables.values()))
    assert table.ref == tpl_table.ref == "A1:Q31"
    assert table.tableStyleInfo.name == tpl_table.tableStyleInfo.name
    assert [c.name for c in table.tableColumns] == [c.name for c in tpl_table.tableColumns]
    assert ws.sheet_view.rightToLeft and ws.freeze_panes == tpl.freeze_panes
    assert {k: d.width for k, d in ws.column_dimensions.items() if d.width} == {
        k: d.width for k, d in tpl.column_dimensions.items() if d.width
    }
    inputs = {D, H, I_MID, J_EDGE, Q_NOTES, 1, 2, 3, E, F, G}  # weekday, month, day and the times differ
    for row in tpl.iter_rows(min_row=1, max_row=35, max_col=17):
        for source in row:
            target = ws[source.coordinate]
            assert _style(target) == _style(source), source.coordinate
            if 2 <= source.row <= 31 and source.column in inputs:
                continue
            want = source.value
            if isinstance(want, str) and want.startswith("="):
                want = want.replace(tpl_table.displayName, table.displayName)
            assert target.value == want, source.coordinate
            if source.comment is not None:
                assert target.comment is not None and target.comment.text == source.comment.text
    # The totals block, as in آبان (B33 «مرخصی مجاز» is left for the admin)
    assert ws["Q35"].value == f"=B32+MIN(L32, Q32)+SUM({table.displayName}[مرخصي اول و آخر])"
    assert ws["B34"].value == (f"=SUM({table.displayName}[مرخصي اول و آخر])+SUM({table.displayName}[مرخصی وسط روز])")
    assert ws["M34"].value == time(7, 0) and ws["M35"].value == time(3, 0) and ws["B33"].value is None


async def test_inputs_of_the_month(services, make_user, clock) -> None:
    content, _ = await _month_workbook(services, make_user, clock)
    ws = openpyxl.load_workbook(io.BytesIO(content))["مریم کریمی"]
    row = 26  # 17/10/2026 = 25 مهر
    assert (ws.cell(row, 1).value, ws.cell(row, 2).value, ws.cell(row, 3).value) == ("شنبه", "مهر", 25)
    assert ws.cell(row, D).value == time(8, 38) and ws.cell(row, H).value == time(17, 6)
    assert (ws.cell(row, E).value, ws.cell(row, F).value, ws.cell(row, G).value) == (time(8), time(9, 30), time(16, 48))
    assert ws.cell(row, I_MID).value is None and ws.cell(row, J_EDGE).value is None  # leave: not defined yet
    assert "ثبت فراموشی" in ws.cell(row, Q_NOTES).value
    # Thursday 30 مهر is a day off: E = F = G = the check-in, so the formulas give all overtime
    assert [ws.cell(31, col).value for col in (D, E, F, G)] == [time(9, 50)] * 4
    assert "کل حضور اضافه‌کار" in ws.cell(31, Q_NOTES).value
    assert ws.cell(2, 1).value == "چهارشنبه" and ws.cell(2, 3).value == 1  # 1 مهر 1405 is a Wednesday


@pytest.mark.parametrize(
    ("year", "month", "days"),
    [(1405, 1, 31), (1404, 12, 29), (1405, 8, 30)],  # فروردین, اسفند (not a leap year), آبان
)
async def test_the_structure_is_recreated_for_months_of_any_length(
    services, make_user, clock, year, month, days
) -> None:
    user = await make_user("کارمند")
    start, end = month_range(to_gregorian(year, month, 1))
    report = await services.attendance.employee_report(user, start, end)
    ws = openpyxl.load_workbook(
        io.BytesIO(build_timesheet_workbook([report], services.attendance.sheet_rules, services.attendance.holidays))
    ).worksheets[0]
    table = next(iter(ws.tables.values()))
    last = days + 1
    assert table.ref == f"A1:Q{last}"
    assert [ws.cell(r, 3).value for r in (2, last)] == [1, days] and ws.cell(last + 1, 3).value is None
    t = table.displayName
    # The totals block sits right under the last day, its references moved with it.
    first = last + 1
    assert ws[f"A{first}"].value == "مجموع کارکرد عادی:" and ws[f"P{first + 3}"].value == "کارکرد نهایی:"
    assert ws[f"Q{first}"].value == f"=L{first + 1}+MIN(L{first + 2}, M{first + 2})+MIN(L{first + 3}, M{first + 3})"
    assert ws[f"Q{first + 3}"].value == f"=B{first}+MIN(L{first}, Q{first})+SUM({t}[مرخصي اول و آخر])"
    assert ws[f"N{first + 2}"].value == f"=MAX(L{first + 2}-M{first + 2},0)"
    assert ws[f"P{first}"].comment is not None


async def test_a_week_and_hourly_staff_use_the_same_structure(services, make_user, clock) -> None:
    user = await make_user("نیروی ساعتی")
    admin = await make_user("مدیر", __import__("app.core.security.rbac", fromlist=["RoleCode"]).RoleCode.SUPER_ADMIN)
    await services.users.set_hourly(admin, user.id, True)
    clock.set(tehran(2026, 10, 19, 20, 0))
    await services.attendance.submit_correction(
        user, CorrectionInput(CorrectionType.BOTH, date(2026, 10, 19), time(10, 15), time(14, 0), same_day=True)
    )
    report = await services.attendance.my_week(user)
    ws = openpyxl.load_workbook(
        io.BytesIO(build_timesheet_workbook([report], services.attendance.sheet_rules, services.attendance.holidays))
    ).worksheets[0]
    assert next(iter(ws.tables.values())).ref == "A1:Q8"  # Saturday–Friday
    assert ws["A9"].value == "مجموع کارکرد عادی:"
    row = 4  # Monday 27 مهر
    assert ws.cell(row, 3).value == 27
    # Hourly: no official hours, so the window is the presence itself (no lateness, no early departure)
    assert [ws.cell(row, c).value for c in (D, E, F, G, H)] == [time(10, 15)] * 3 + [time(14, 0)] * 2


def _minutes(value) -> int:
    if isinstance(value, dt.time):
        return value.hour * 60 + value.minute + round(value.second / 60)
    if isinstance(value, dt.timedelta):
        return round(value.total_seconds() / 60)
    if isinstance(value, dt.datetime):  # a duration read as a date
        from openpyxl.utils.datetime import to_excel

        return round(to_excel(value) * 1440)
    return round((value or 0) * 1440)


@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice is not installed")
async def test_libreoffice_recalculation_matches_the_bot(services, make_user, clock, tmp_path: Path) -> None:
    content, report = await _month_workbook(services, make_user, clock)
    source = tmp_path / "in" / "timesheet.xlsx"
    source.parent.mkdir()
    source.write_bytes(content)
    out = tmp_path / "out"
    env = {**os.environ, "HOME": str(tmp_path)}
    await asyncio.to_thread(
        subprocess.run,
        ["soffice", "--headless", "--calc", "--convert-to", "xlsx", "--outdir", str(out), str(source)],
        check=True,
        capture_output=True,
        env=env,
        timeout=120,
    )
    ws = openpyxl.load_workbook(out / "timesheet.xlsx", data_only=True).active
    rows = {r.work_date: r for r in report.rows}
    for day, _, _ in DAYS:
        excel_row = to_jalali(day).day + 1
        c = rows[day].calculation
        got = [_minutes(ws.cell(excel_row, col).value) for col in range(K_USEFUL, P_OVERTIME + 1)]
        want = [
            c.gross_worked_minutes,
            c.normal_minutes,
            c.floating_late_minutes,
            c.late_minutes,
            c.early_departure_minutes,
            c.overtime_minutes,
        ]
        assert got == want, day
    thursday = rows[date(2026, 10, 22)].calculation
    assert (thursday.overtime_minutes, thursday.late_minutes, thursday.normal_minutes) == (210, 0, 0)
    m = report.sheet
    summary = {
        "B32": m.normal,
        "L32": m.overtime,
        "Q32": m.compensable,
        "L33": m.floating_late,
        "Q33": m.overtime_surplus,
        "L34": m.late,
        "Q34": m.uncompensated,
        "L35": m.early,
        "Q35": m.final_work,  # the bot's own figure, with the organization's current formula (no factors)
    }
    assert {k: _minutes(ws[k].value) for k in summary} == summary


# --- results stored in the file, so every app shows them -----------------------------------------------


@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice is not installed")
async def test_sent_files_carry_their_calculated_results(services, make_user, clock) -> None:
    """Phones, the Telegram preview and Excel's Protected View show stored results only: every formula
    must have one, equal to what the formula gives, while formulas and styles stay as they were."""
    from app.infrastructure.excel.cached_values import with_calculated_values

    content, report = await _month_workbook(services, make_user, clock)
    sent = with_calculated_values(content)
    values = openpyxl.load_workbook(io.BytesIO(sent), data_only=True)["مریم کریمی"]
    before = openpyxl.load_workbook(io.BytesIO(content), data_only=True)["مریم کریمی"]
    assert before["Q35"].value is None  # what openpyxl writes: no results, so viewers show 0
    m = report.sheet
    assert _minutes(values["B32"].value) == m.normal
    assert _minutes(values["L32"].value) == m.overtime
    assert _minutes(values["Q35"].value) == m.final_work
    assert _minutes(values["K26"].value) == 8 * 60 + 28  # 25 مهر: 08:38 → 17:06
    formulas_before = openpyxl.load_workbook(io.BytesIO(content))["مریم کریمی"]
    formulas_after = openpyxl.load_workbook(io.BytesIO(sent))["مریم کریمی"]
    for row in formulas_before.iter_rows():
        for cell in row:
            after = formulas_after[cell.coordinate]
            assert (after.value, after.style_id) == (cell.value, cell.style_id), cell.coordinate
    assert formulas_after.parent.calculation.fullCalcOnLoad  # Excel still recalculates on open


def test_a_file_is_sent_as_it_was_if_libreoffice_fails(monkeypatch) -> None:
    import app.infrastructure.excel.cached_values as cached
    from app.infrastructure.excel.recalc import RecalculationError

    def broken(_content: bytes) -> bytes:
        raise RecalculationError("soffice missing")

    monkeypatch.setattr(cached, "recalculate", broken)
    assert cached.with_calculated_values(b"original") == b"original"
