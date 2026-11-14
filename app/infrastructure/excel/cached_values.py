"""Store the calculated results inside an .xlsx before it is sent, so every app shows the numbers.

A file saved by openpyxl has formulas but no stored results. Desktop Excel recalculates it only after
«Enable Editing» (files from Telegram open in Protected View), and phone viewers and the Telegram
preview never do: every formula then shows 0. So before sending, LibreOffice calculates a copy and its
result for each formula cell is copied into the cached value of that same cell in OUR file. Only those
cached values change: the formulas, formatting, comments and tables are the file's own, and Excel still
recalculates everything when it opens the file (fullCalcOnLoad).

The XML is edited as text (no extra dependency): openpyxl writes every cell as `<c r="A1" …>…</c>`.
"""

import io
import re
import zipfile

from app.core.logging import get_logger
from app.infrastructure.excel.recalc import RecalculationError, recalculate

log = get_logger(__name__)

_CELL = re.compile(r'<c r="([A-Z]{1,3}[0-9]+)"([^>]*?)(?:/>|>(.*?)</c>)', re.S)
_VALUE = re.compile(r"<v>(.*?)</v>", re.S)
_TYPE = re.compile(r'\st="([a-zA-Z]+)"')
_SHEET = re.compile(r'<sheet\b[^>]*\bname="([^"]*)"[^>]*\br:id="([^"]+)"')
_REL = re.compile(r"<Relationship\b[^>]*>")
_ATTR = re.compile(r'(\w+)="([^"]*)"')


def _sheet_paths(book: zipfile.ZipFile) -> dict[str, str]:
    """Sheet name → its XML path in the package."""
    rels = {}
    for rel in _REL.findall(book.read("xl/_rels/workbook.xml.rels").decode("utf-8")):
        attrs = dict(_ATTR.findall(rel))
        target = attrs.get("Target", "").lstrip("/")
        rels[attrs.get("Id")] = target if target.startswith("xl/") else f"xl/{target}"
    names = _SHEET.findall(book.read("xl/workbook.xml").decode("utf-8"))
    return {name: rels[rid] for name, rid in names if rid in rels}


def _results(sheet_xml: str) -> dict[str, tuple[str, str]]:
    """Cell → (type, stored value) for every formula cell LibreOffice calculated."""
    found = {}
    for ref, attrs, inner in _CELL.findall(sheet_xml):
        if not inner or "<f" not in inner:
            continue
        value = _VALUE.search(inner)
        found_type = _TYPE.search(attrs)
        kind = found_type.group(1) if found_type else "n"
        if value is None or kind == "s":  # a shared-string index means nothing in another file
            continue
        found[ref] = (kind, value.group(1))
    return found


def _fill(sheet_xml: str, results: dict[str, tuple[str, str]]) -> tuple[str, int]:
    filled = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal filled
        ref, attrs, inner = match.group(1), match.group(2), match.group(3)
        if not inner or "<f" not in inner or ref not in results:
            return match.group(0)
        kind, value = results[ref]
        formula_end = inner.index("</f>") + len("</f>") if "</f>" in inner else inner.index("/>") + 2
        attrs = _TYPE.sub("", attrs)
        if kind != "n":
            attrs += f' t="{"str" if kind == "inlineStr" else kind}"'
        filled += 1
        return f'<c r="{ref}"{attrs}>{inner[:formula_end]}<v>{value}</v></c>'

    return _CELL.sub(replace, sheet_xml), filled


def merge_calculated_values(content: bytes, calculated: bytes) -> bytes:
    """Copy LibreOffice's result for each formula cell into `content` (matched by sheet name and cell)."""
    ours = zipfile.ZipFile(io.BytesIO(content))
    theirs = zipfile.ZipFile(io.BytesIO(calculated))
    their_paths = _sheet_paths(theirs)
    replacements: dict[str, bytes] = {}
    total = 0
    for name, path in _sheet_paths(ours).items():
        if name not in their_paths:
            continue
        results = _results(theirs.read(their_paths[name]).decode("utf-8"))
        xml, filled = _fill(ours.read(path).decode("utf-8"), results)
        if filled:
            replacements[path] = xml.encode("utf-8")
            total += filled
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as out:
        for item in ours.infolist():
            out.writestr(item, replacements.get(item.filename, ours.read(item.filename)))
    log.info("stored calculated values", cells=total)
    return buffer.getvalue()


def with_calculated_values(content: bytes) -> bytes:
    """The file with its results stored, ready to send. If LibreOffice fails, the file as it was
    (Excel still calculates it after «Enable Editing»)."""
    try:
        return merge_calculated_values(content, recalculate(content))
    except (RecalculationError, OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        log.warning("could not store calculated values; sending the file without them", error=str(exc))
        return content
