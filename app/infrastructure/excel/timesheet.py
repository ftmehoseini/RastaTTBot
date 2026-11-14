"""The organization's timesheet: every attendance Excel file the bot sends has this one structure.

The structure is the organization's own «آبان» sheet, kept as a template in
`templates/timesheet_template.xlsx` (layout, formulas, totals block, styles and the notes on the
totals; no personal data). For each employee the template sheet is recreated for the report's days:
one row per day (weekday, month, day, the inputs), the template's formulas in the calculated columns
(their table name renamed), and the totals block moved under the last day. Only the inputs are
written by the bot; Excel (or LibreOffice) calculates everything when the file is opened.

Columns are found by their header text, so a new template with the same headers needs no code change.
The two leave columns («مرخصی وسط روز», «مرخصي اول و آخر») are left empty until their rules are defined;
an approved leave is described in «توضیحات».
"""

import io
import re
from collections.abc import Mapping, Sequence
from copy import copy
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from uuid import UUID

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableColumn, TableFormula, TableStyleInfo
from openpyxl.worksheet.worksheet import Worksheet

from app.application.dto.attendance import DayRow, EmployeeReport
from app.core.utils.jalali import iter_days, month_name, to_jalali, weekday_name
from app.domain.attendance.models import DayStatus
from app.domain.attendance.timesheet import SheetRules
from app.infrastructure.excel.workbook import cell_minutes

TEMPLATE_PATH = Path(__file__).parent / "templates" / "timesheet_template.xlsx"

WEEKDAY = "روز"
MONTH = "ماه"
DAY = "تاریخ"
CHECK_IN = "ساعت ورود"
FLOAT_START = "شروع شناوری"
FLOAT_END = "پایان شناوری"
ALLOWED_EXIT = "ساعت مجاز خروج"
CHECK_OUT = "ساعت خروج"
NOTES = "توضیحات"
MIDDAY_LEAVE = "مرخصی وسط روز"
EDGE_LEAVE = "مرخصی اول و آخر"  # as normalized (the file writes it with Arabic «ي»)
REQUIRED = (WEEKDAY, MONTH, DAY, CHECK_IN, FLOAT_START, FLOAT_END, ALLOWED_EXIT, CHECK_OUT)

# A1-style reference (B32, $L$34, …) in a formula; table names and structured references don't match.
_CELL_REF = re.compile(r"(?<![A-Za-z0-9_؀-ۿ\[])(\$?)([A-Z]{1,3})(\$?)(\d+)(?![\d(])")


def _key(text: object) -> str:
    return " ".join(str(text).replace("ي", "ی").replace("ك", "ک").split())


@dataclass(frozen=True)
class _Template:
    sheet: Worksheet
    table: Table
    columns: dict[str, int]  # normalized header -> column
    last_col: int
    day_row: int  # the template's first day row (day n copies template row day_row + n - 1)
    last_day_row: int
    totals: list[int]  # rows of the totals block under the day rows


def _template(workbook: Workbook) -> _Template:
    sheet = workbook.worksheets[0]
    if not sheet.tables:
        raise ValueError("timesheet template: the sheet must contain an Excel table")
    table = next(iter(sheet.tables.values()))
    match = re.fullmatch(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", table.ref)
    assert match is not None, table.ref
    header_row, last_day_row = int(match.group(2)), int(match.group(4))
    columns = {_key(c.value): c.column for c in sheet[header_row] if c.value}
    missing = [h for h in REQUIRED if h not in columns]
    if missing:
        raise ValueError(f"timesheet template: missing columns {missing}")
    totals = [r for r in range(last_day_row + 1, sheet.max_row + 1) if any(c.value is not None for c in sheet[r])]
    last_col = max(columns.values())
    return _Template(sheet, table, columns, last_col, header_row + 1, last_day_row, totals)


def _shift_rows(formula: str, offset: int) -> str:
    """Move A1 references by `offset` rows (the totals block moves with the number of days)."""

    def shift(m: re.Match[str]) -> str:
        if m.group(3):  # $-anchored row stays
            return m.group(0)
        return f"{m.group(1)}{m.group(2)}{m.group(3)}{int(m.group(4)) + offset}"

    return _CELL_REF.sub(shift, formula)


def _copy_cell(source, target, value=None) -> None:
    target._style = copy(source._style)
    target.value = source.value if value is None else value
    if source.comment is not None:
        comment = Comment(source.comment.text, source.comment.author)
        comment.width, comment.height = source.comment.width, source.comment.height
        target.comment = comment


def _sheet_title(name: str, used: set[str]) -> str:
    base = re.sub(r"[\[\]:*?/\\]", " ", name).strip()[:28] or "کارمند"
    title, n = base, 2
    while title in used:
        title, n = f"{base[:25]} {n}", n + 1
    used.add(title)
    return title


def _clock(minutes: int) -> time | timedelta:
    """Excel time value: a time of day, or more than a day for a check-out after midnight."""
    if 0 <= minutes < 1440:
        return time(minutes // 60, minutes % 60)
    return timedelta(minutes=minutes)


def _minutes_of(day: date, value: datetime) -> int:
    midnight = datetime.combine(day, time(0, 0), tzinfo=value.tzinfo)
    return int((value - midnight).total_seconds() // 60)


def _notes(row: DayRow | None, day: date, holidays: Mapping[date, str]) -> str:
    notes = []
    if day in holidays:
        notes.append(f"تعطیل رسمی: {holidays[day]}")
    if row is not None:
        if not row.is_workday and row.calculation.check_in is not None:
            notes.append("روز تعطیل: کل حضور اضافه‌کار")
        if row.leave_note:
            notes.append(row.leave_note)
        status = row.calculation.status
        if status == DayStatus.MISSING_CHECK_OUT:
            notes.append("خروج ثبت نشده")
        elif status == DayStatus.MISSING_CHECK_IN:
            notes.append("ورود ثبت نشده")
        if row.is_forgotten:
            notes.append("ثبت فراموشی")
        if row.is_manual_today:
            notes.append("ثبت دستی")
        if row.is_edited:
            notes.append("ویرایش‌شده")
    return " | ".join(notes)


def _inputs(row: DayRow | None, day: date, hourly: bool, rules: SheetRules) -> dict[str, object]:
    """The input cells of one day, by header. Calculated columns are left to the template's formulas."""
    values: dict[str, object] = {
        WEEKDAY: weekday_name(day),
        MONTH: month_name(to_jalali(day).month),
        DAY: to_jalali(day).day,
        FLOAT_START: _clock(rules.floating_start),
        FLOAT_END: _clock(rules.floating_end),
        ALLOWED_EXIT: _clock(rules.allowed_exit),
    }
    c = row.calculation if row else None
    # A check-out without a check-in is left out: the formulas would read it as presence from 0:00.
    if c is not None and c.check_in is not None:
        check_in = _minutes_of(day, c.check_in)
        values[CHECK_IN] = _clock(check_in)
        check_out = _minutes_of(day, c.check_out) if c.check_out is not None else None
        if check_out is not None:
            values[CHECK_OUT] = _clock(check_out)
        if hourly:
            # No official hours: start = end of window = check-in and allowed exit = check-out, so the
            # formulas give the presence as normal work, with no lateness or early departure.
            values[FLOAT_START] = values[FLOAT_END] = _clock(check_in)
            if check_out is not None:
                values[ALLOWED_EXIT] = _clock(check_out)
        elif row is not None and not row.is_workday:
            # Day off: E = F = G = the check-in makes the same formulas count all presence as overtime.
            for header in (FLOAT_START, FLOAT_END, ALLOWED_EXIT):
                values[header] = _clock(check_in)
    return values


def _from_file(values: dict[str, object], file_row: Mapping[str, object], rules: SheetRules) -> None:
    """Take what the admin set in the person's own Excel file for this day (e.g. a registered leave):
    E/F/G where they differ from the official times, the two leave columns and the notes.
    Check-in and check-out stay the bot's records (the bot writes them into the file too)."""
    official = {FLOAT_START: rules.floating_start, FLOAT_END: rules.floating_end, ALLOWED_EXIT: rules.allowed_exit}
    for header, default in official.items():
        minutes = cell_minutes(file_row.get(_key(header)))
        if minutes is not None and minutes != default:
            values[header] = _clock(minutes)
    for header in (MIDDAY_LEAVE, EDGE_LEAVE):
        minutes = cell_minutes(file_row.get(_key(header)))
        if minutes:
            values[_key(header)] = _clock(minutes)
    note = file_row.get(_key(NOTES))
    if isinstance(note, str) and note.strip() and not note.startswith("="):
        current = values.get(NOTES)
        if not current:
            values[NOTES] = note.strip()
        elif note.strip() not in str(current):
            values[NOTES] = f"{current} | {note.strip()}"


def add_timesheet_sheet(
    workbook: Workbook,
    template: _Template,
    report: EmployeeReport,
    rules: SheetRules,
    holidays: Mapping[date, str],
    table_name: str,
    title: str,
    file_rows: Mapping[date, Mapping[str, object]] | None = None,
) -> Worksheet:
    tpl = template.sheet
    old_name = template.table.displayName
    sheet = workbook.create_sheet(title)
    sheet.sheet_view.rightToLeft = tpl.sheet_view.rightToLeft
    sheet.sheet_view.zoomScale = tpl.sheet_view.zoomScale
    sheet.freeze_panes = tpl.freeze_panes
    for key, dim in tpl.column_dimensions.items():
        sheet.column_dimensions[key].width = dim.width
        sheet.column_dimensions[key].hidden = dim.hidden

    header_row = template.day_row - 1
    for col in range(1, template.last_col + 1):
        _copy_cell(tpl.cell(header_row, col), sheet.cell(header_row, col))
    sheet.row_dimensions[header_row].height = tpl.row_dimensions[header_row].height

    rows = {r.work_date: r for r in report.rows}
    days = iter_days(report.start, report.end)
    for index, day in enumerate(days):
        r = template.day_row + index
        row = rows.get(day)
        values = _inputs(row, day, report.hourly, rules)
        values[NOTES] = _notes(row, day, holidays) or None
        if file_rows and day in file_rows:
            _from_file(values, file_rows[day], rules)
        by_column = {template.columns[h]: v for h, v in values.items() if h in template.columns}
        # Day n copies the template's row n (its exact styles); a longer month repeats the last row.
        source_row = min(template.day_row + index, template.last_day_row)
        for col in range(1, template.last_col + 1):
            source = tpl.cell(source_row, col)
            target = sheet.cell(r, col)
            target._style = copy(source._style)
            if isinstance(source.value, str) and source.value.startswith("="):
                target.value = source.value.replace(old_name, table_name)
            else:
                target.value = by_column.get(col)
        sheet.row_dimensions[r].height = tpl.row_dimensions[source_row].height
    last = template.day_row + len(days) - 1

    offset = last - template.last_day_row
    for r in template.totals:
        for source in tpl[r]:
            if source.value is None and not source.has_style and source.comment is None:
                continue
            target = sheet.cell(r + offset, source.column)
            value = source.value
            if isinstance(value, str) and value.startswith("="):
                value = _shift_rows(value.replace(old_name, table_name), offset)
            _copy_cell(source, target, value)
        if tpl.row_dimensions[r].height:
            sheet.row_dimensions[r + offset].height = tpl.row_dimensions[r].height

    table = Table(displayName=table_name, ref=f"A{header_row}:{get_column_letter(template.last_col)}{last}")
    style = template.table.tableStyleInfo
    table.tableStyleInfo = TableStyleInfo(
        name=style.name,
        showFirstColumn=style.showFirstColumn,
        showLastColumn=style.showLastColumn,
        showRowStripes=style.showRowStripes,
        showColumnStripes=style.showColumnStripes,
    )
    # Declaring the calculated columns lets Excel fill the formulas into rows added by hand.
    table.tableColumns = [
        TableColumn(
            id=i,
            name=column.name,
            calculatedColumnFormula=(
                TableFormula(attr_text=column.calculatedColumnFormula.attr_text.replace(old_name, table_name))
                if column.calculatedColumnFormula is not None
                else None
            ),
        )
        for i, column in enumerate(template.table.tableColumns, start=1)
    ]
    sheet.add_table(table)
    return sheet


def build_timesheet_workbook(
    reports: Sequence[EmployeeReport],
    rules: SheetRules,
    holidays: Mapping[date, str],
    template_path: Path = TEMPLATE_PATH,
    file_rows: Mapping[UUID, Mapping[date, Mapping[str, object]]] | None = None,
) -> bytes:
    """One sheet per employee in the organization's structure, for each report's days.

    `file_rows` holds, per person who has their own Excel file, that file's stored day rows: what the
    admin set there (leave, changed times, notes) is shown in the export too.
    """
    workbook = load_workbook(template_path)
    template = _template(workbook)
    used: set[str] = set()
    for i, report in enumerate(reports, start=1):
        month = month_name(to_jalali(report.start).month)
        add_timesheet_sheet(
            workbook,
            template,
            report,
            rules,
            holidays,
            f"{month}_{i}",
            _sheet_title(report.full_name, used),
            (file_rows or {}).get(report.user_id),
        )
    if reports:
        workbook.remove(template.sheet)
    else:
        for row in template.sheet.iter_rows():
            for cell in row:
                cell.value = None
                cell.comment = None
        template.sheet.tables.clear()
        template.sheet.title = "بدون داده"
        template.sheet["A1"] = "کارمندی در این گزارش نیست."
    workbook.calculation.fullCalcOnLoad = True  # Excel and LibreOffice recalculate on open
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
