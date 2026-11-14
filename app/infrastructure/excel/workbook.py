"""Admin-uploaded attendance workbooks: find the cells by their labels, write check-in/out, read results.

The bot knows nothing about the formulas. It relies only on the labels every organization timesheet
already has:

* a sheet per Jalali month, named after the month («آبان», «آذر», …),
* a header row with «تاریخ» (day of the month), «ساعت ورود» and «ساعت خروج»,
* under the day rows, totals written as «label:» followed by the value cell («کارکرد نهایی:» …).

Writing changes only the check-in and check-out cells, so the admin's formulas, formatting and
comments stay as they are. Results are read from a copy recalculated by LibreOffice
(see recalc.py), never from the stored file.
"""

import io
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from uuid import UUID

from openpyxl import load_workbook
from openpyxl.utils.datetime import to_excel
from openpyxl.worksheet.worksheet import Worksheet

from app.core.utils.jalali import MONTH_NAMES
from app.core.utils.text import normalize_digits

DATE_HEADER = "تاریخ"
CHECK_IN_HEADER = "ساعت ورود"
CHECK_OUT_HEADER = "ساعت خروج"
REQUIRED_HEADERS = (DATE_HEADER, CHECK_IN_HEADER, CHECK_OUT_HEADER)
# Columns that only identify the row; they are not part of the report.
ROW_LABEL_HEADERS = {"روز", "ماه", DATE_HEADER}
NOTES_HEADER = "توضیحات"
HEADER_SEARCH_ROWS = 10
MAX_DAY_ROWS = 40


class WorkbookError(Exception):
    """The file can't be used: a Persian explanation for the admin is in `str(exc)`."""


def normalize_label(value: object) -> str:
    """Header/label text compared loosely: Arabic ي/ك, extra spaces and a trailing colon ignored."""
    text = str(value).replace("ي", "ی").replace("ك", "ک").replace("‌", " ")
    return " ".join(text.split()).rstrip(":：").strip()


def _month_of_title(title: str) -> int | None:
    words = normalize_label(title).split()
    for index, name in enumerate(MONTH_NAMES, start=1):
        if words and words[0] == name:
            return index
    return None


def _day_number(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return int(value) if float(value).is_integer() else None
    text = normalize_digits(str(value)).strip()
    return int(text) if text.isdigit() else None


@dataclass(frozen=True)
class SheetLayout:
    title: str
    month: int
    header_row: int
    columns: dict[str, int]  # normalized header -> column number
    labels: dict[int, str]  # column number -> header as written
    day_rows: dict[int, int]  # day of month -> row number
    last_day_row: int

    def column(self, header: str) -> int:
        return self.columns[header]


def _layout(sheet: Worksheet, month: int) -> SheetLayout | None:
    for row in range(1, HEADER_SEARCH_ROWS + 1):
        cells = {normalize_label(c.value): c.column for c in sheet[row] if isinstance(c.value, str) and c.value.strip()}
        if all(h in cells for h in REQUIRED_HEADERS):
            labels = {col: " ".join(str(sheet.cell(row, col).value).split()) for col in cells.values()}
            day_rows: dict[int, int] = {}
            last = row
            for r in range(row + 1, row + 1 + MAX_DAY_ROWS):
                day = _day_number(sheet.cell(r, cells[DATE_HEADER]).value)
                if day is None or not 1 <= day <= 31:
                    continue
                day_rows.setdefault(day, r)
                last = r
            return SheetLayout(sheet.title, month, row, cells, labels, day_rows, last)
    return None


def month_sheets(workbook) -> dict[int, SheetLayout]:
    """Every sheet named after a Jalali month that has the required headers, by month number."""
    found: dict[int, SheetLayout] = {}
    for sheet in workbook.worksheets:
        month = _month_of_title(sheet.title)
        if month is None or month in found:
            continue
        layout = _layout(sheet, month)
        if layout is not None and layout.day_rows:
            found[month] = layout
    return found


@dataclass(frozen=True)
class WorkbookInfo:
    sheets: list[str]  # month sheet titles in calendar order


def inspect_workbook(content: bytes) -> WorkbookInfo:
    """Check an uploaded file before it is stored. Raises WorkbookError with a Persian reason."""
    try:
        workbook = load_workbook(io.BytesIO(content))
    except Exception as exc:
        raise WorkbookError("فایل اکسل باز نشد. فقط فایل ‎.xlsx‎ سالم پذیرفته می‌شود.") from exc
    sheets = month_sheets(workbook)
    if not sheets:
        raise WorkbookError(
            "در این فایل هیچ برگه‌ای با نام ماه («فروردین»، «اردیبهشت»، …) پیدا نشد که ستون‌های "
            f"«{DATE_HEADER}»، «{CHECK_IN_HEADER}» و «{CHECK_OUT_HEADER}» را داشته باشد."
        )
    return WorkbookInfo([sheets[m].title for m in sorted(sheets)])


# --- writing ------------------------------------------------------------------------------------------


def _clock(minutes: int) -> time | timedelta:
    """Excel time value: a time of day, or more than a day for a check-out after midnight."""
    if 0 <= minutes < 1440:
        return time(minutes // 60, minutes % 60)
    return timedelta(minutes=minutes)


@dataclass(frozen=True)
class DayTimes:
    """Minutes after midnight of the work date; None leaves the cell as it is."""

    day: int
    check_in: int | None
    check_out: int | None


def write_times(content: bytes, month: int, days: list[DayTimes]) -> bytes:
    """Write check-in/out into the month sheet. Only those cells change. Raises WorkbookError."""
    workbook = load_workbook(io.BytesIO(content))
    layout = month_sheets(workbook).get(month)
    if layout is None:
        raise WorkbookError(f"برگه‌ی ماه «{MONTH_NAMES[month - 1]}» در فایل اکسل این کارمند نیست.")
    sheet = workbook[layout.title]
    for item in days:
        row = layout.day_rows.get(item.day)
        if row is None:
            raise WorkbookError(f"ردیف روز {item.day} در برگه‌ی «{layout.title}» پیدا نشد.")
        for header, minutes in ((CHECK_IN_HEADER, item.check_in), (CHECK_OUT_HEADER, item.check_out)):
            if minutes is None:
                continue
            cell = sheet.cell(row, layout.column(header))
            if isinstance(cell.value, str) and cell.value.startswith("="):
                raise WorkbookError(f"خانه‌ی «{header}» روز {item.day} در برگه‌ی «{layout.title}» فرمول دارد.")
            cell.value = _clock(minutes)
    # Excel and LibreOffice recalculate every formula when the file is opened.
    workbook.calculation.fullCalcOnLoad = True
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


# --- reading ------------------------------------------------------------------------------------------


def _minutes(value: object, number_format: str) -> int | None:
    if isinstance(value, datetime):
        # A duration read as a date (Excel serial number); openpyxl converts it back exactly,
        # including Excel's 1900 leap-year quirk.
        return round(to_excel(value) * 1440)
    if isinstance(value, timedelta):
        return round(value.total_seconds() / 60)
    if isinstance(value, time):
        return value.hour * 60 + value.minute + round(value.second / 60)
    if isinstance(value, int | float) and not isinstance(value, bool) and re.search(r"[hHmM]", number_format):
        return round(value * 1440)
    return None


def format_duration(minutes: int) -> str:
    sign = "-" if minutes < 0 else ""
    minutes = abs(minutes)
    return f"{sign}{minutes // 60}:{minutes % 60:02d}"


def display_value(value: object, number_format: str = "General") -> str | None:
    """A result cell as text: durations as H:MM, numbers as numbers, text as written. None if empty."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    minutes = _minutes(value, number_format)
    if minutes is not None:
        return format_duration(minutes)
    if isinstance(value, float):
        return f"{value:g}"
    return " ".join(str(value).split())


@dataclass
class WorkbookReport:
    sheet: str
    day: int
    day_values: list[tuple[str, str]] = field(default_factory=list)  # (column header, value)
    month_values: list[tuple[str, str]] = field(default_factory=list)  # (label, value)
    errors: list[str] = field(default_factory=list)  # formula errors such as #VALUE!

    @property
    def ok(self) -> bool:
        return not self.errors


def _is_error(value: object) -> bool:
    return isinstance(value, str) and value.strip().startswith("#") and value.strip().upper() != "#"


def read_report(recalculated: bytes, month: int, day: int, expected: DayTimes | None = None) -> WorkbookReport:
    """Read a day's row and the month's labelled totals from a recalculated copy."""
    workbook = load_workbook(io.BytesIO(recalculated), data_only=True)
    layout = month_sheets(workbook).get(month)
    if layout is None:
        raise WorkbookError(f"برگه‌ی ماه «{MONTH_NAMES[month - 1]}» در فایل محاسبه‌شده پیدا نشد.")
    sheet = workbook[layout.title]
    row = layout.day_rows.get(day)
    if row is None:
        raise WorkbookError(f"ردیف روز {day} در برگه‌ی «{layout.title}» پیدا نشد.")
    report = WorkbookReport(sheet=layout.title, day=day)

    for col in sorted(layout.labels):
        header = layout.labels[col]
        if normalize_label(header) in ROW_LABEL_HEADERS:
            continue
        cell = sheet.cell(row, col)
        if _is_error(cell.value):
            report.errors.append(f"{header}: {cell.value}")
            continue
        shown = display_value(cell.value, cell.number_format)
        if shown is not None:
            report.day_values.append((header, shown))

    if expected is not None:
        # The values read back must be the ones just written: same person's file, same day.
        for header, minutes in ((CHECK_IN_HEADER, expected.check_in), (CHECK_OUT_HEADER, expected.check_out)):
            if minutes is None:
                continue
            cell = sheet.cell(row, layout.column(header))
            if _minutes(cell.value, cell.number_format) != minutes:
                report.errors.append(f"{header}: مقدار خوانده‌شده با مقدار ثبت‌شده یکی نیست")

    report.month_values = _month_totals(sheet, layout, report.errors)
    return report


def _month_totals(sheet: Worksheet, layout: SheetLayout, errors: list[str]) -> list[tuple[str, str]]:
    """Every «label:» under the day rows with the first value to its right (formula errors go to `errors`)."""
    values: list[tuple[str, str]] = []
    for r in range(layout.last_day_row + 1, sheet.max_row + 1):
        cells = list(sheet[r])
        for index, cell in enumerate(cells):
            if not (isinstance(cell.value, str) and cell.value.strip().endswith((":", "："))):
                continue
            target = next((c for c in cells[index + 1 :] if c.value is not None and str(c.value).strip()), None)
            if target is None or (isinstance(target.value, str) and target.value.strip().endswith((":", "："))):
                continue
            label = normalize_label(cell.value)
            if _is_error(target.value):
                errors.append(f"{label}: {target.value}")
                continue
            shown = display_value(target.value, target.number_format)
            if shown is not None:
                values.append((label, shown))
    return values


def formula_columns(source: bytes) -> dict[int, set[int]]:
    """Per month sheet, the columns the admin's formulas calculate (a formula in any day row).

    Read from the stored file (formulas), not the recalculated copy (values only).
    """
    workbook = load_workbook(io.BytesIO(source))
    result: dict[int, set[int]] = {}
    for month, layout in month_sheets(workbook).items():
        sheet = workbook[layout.title]
        result[month] = {
            col
            for col in layout.labels
            if any(
                isinstance(v := sheet.cell(row, col).value, str) and v.startswith("=")
                for row in layout.day_rows.values()
            )
        }
    return result


@dataclass
class PeriodDay:
    month: int
    day: int
    check_in: str | None
    check_out: str | None
    values: list[tuple[str, str]]  # the formula columns of that row (header, value)
    notes: str | None


@dataclass
class PeriodReport:
    sheet: str  # the month whose totals are given
    days: list[PeriodDay] = field(default_factory=list)
    month_values: list[tuple[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _cell_text(sheet: Worksheet, layout: SheetLayout, row: int, col: int, where: str, errors: list[str]) -> str | None:
    cell = sheet.cell(row, col)
    if _is_error(cell.value):
        errors.append(f"{where} {layout.labels.get(col, '')}: {cell.value}")
        return None
    return display_value(cell.value, cell.number_format)


def read_period(source: bytes, recalculated: bytes, days: list[tuple[int, int]], summary_month: int) -> PeriodReport:
    """Several days' rows (month, day) and one month's labelled totals, from one recalculated copy.

    Days whose month sheet is missing are left out; a missing `summary_month` sheet is an error.
    """
    calculated_columns = formula_columns(source)
    workbook = load_workbook(io.BytesIO(recalculated), data_only=True)
    sheets = month_sheets(workbook)
    summary = sheets.get(summary_month)
    if summary is None:
        raise WorkbookError(f"برگه‌ی ماه «{MONTH_NAMES[summary_month - 1]}» در فایل اکسل نیست.")
    report = PeriodReport(sheet=summary.title)
    for month, day in days:
        layout = sheets.get(month)
        row = layout.day_rows.get(day) if layout else None
        if layout is None or row is None:
            continue
        sheet = workbook[layout.title]

        def cell_text(col: int, sheet=sheet, layout=layout, row=row, day=day) -> str | None:
            return _cell_text(sheet, layout, row, col, f"{layout.title} {day}", report.errors)

        values = []
        for col in sorted(calculated_columns.get(month, set())):
            shown = cell_text(col)
            if shown is not None:
                values.append((layout.labels[col], shown))
        notes_col = layout.columns.get(NOTES_HEADER)
        report.days.append(
            PeriodDay(
                month=month,
                day=day,
                check_in=cell_text(layout.column(CHECK_IN_HEADER)),
                check_out=cell_text(layout.column(CHECK_OUT_HEADER)),
                values=values,
                notes=cell_text(notes_col) if notes_col else None,
            )
        )
    report.month_values = _month_totals(workbook[summary.title], summary, report.errors)
    return report


# --- storage --------------------------------------------------------------------------------------------


class WorkbookStore:
    """Every version of every workbook on the data volume: <root>/<id>/v0001.xlsx, v0002.xlsx, …"""

    def __init__(self, root: Path, keep_versions: int = 50):
        self.root = Path(root)
        self.keep_versions = keep_versions

    def path(self, workbook_id: UUID, version: int) -> Path:
        return self.root / workbook_id.hex / f"v{version:04d}.xlsx"

    def save(self, workbook_id: UUID, version: int, content: bytes) -> Path:
        """Atomic: a reader sees the old file or the complete new one, never half a file."""
        target = self.path(workbook_id, version)
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(".partial")
        partial.write_bytes(content)
        os.replace(partial, target)
        self._prune(target.parent)
        return target

    def read(self, workbook_id: UUID, version: int) -> bytes:
        path = self.path(workbook_id, version)
        if not path.is_file():
            raise WorkbookError("فایل اکسل این کارمند روی سرور پیدا نشد. لطفاً دوباره بارگذاری کنید.")
        return path.read_bytes()

    def _prune(self, directory: Path) -> None:
        versions = sorted(directory.glob("v*.xlsx"))
        for old in versions[: max(0, len(versions) - self.keep_versions)]:
            old.unlink(missing_ok=True)
