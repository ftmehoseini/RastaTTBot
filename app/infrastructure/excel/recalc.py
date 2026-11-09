"""Recalculate a workbook's formulas with headless LibreOffice.

openpyxl can write formulas but can't calculate them. LibreOffice opens a copy, calculates every
formula and saves the values, which openpyxl then reads (data_only=True). Each run gets its own
temporary profile, so several runs at once don't block each other. The input is never modified.
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.core.logging import get_logger

log = get_logger(__name__)

DEFAULT_TIMEOUT_SECONDS = 120


class RecalculationError(Exception):
    pass


def libreoffice_binary() -> str | None:
    return shutil.which("soffice") or shutil.which("libreoffice")


def recalculate(content: bytes, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> bytes:
    """Return the workbook with every formula's value calculated. Raises RecalculationError."""
    binary = libreoffice_binary()
    if binary is None:
        raise RecalculationError("LibreOffice (soffice) is not installed")
    with tempfile.TemporaryDirectory(prefix="recalc-") as tmp:
        root = Path(tmp)
        source = root / "in" / "book.xlsx"
        source.parent.mkdir()
        source.write_bytes(content)
        out = root / "out"
        command = [
            binary,
            f"-env:UserInstallation={(root / 'profile').as_uri()}",
            "--headless",
            "--norestore",
            "--calc",
            "--convert-to",
            "xlsx",
            "--outdir",
            str(out),
            str(source),
        ]
        try:
            completed = subprocess.run(
                command, capture_output=True, timeout=timeout, env={**os.environ, "HOME": tmp}, check=False
            )
        except subprocess.TimeoutExpired as exc:
            raise RecalculationError(f"LibreOffice did not finish in {timeout} s") from exc
        result = out / "book.xlsx"
        if completed.returncode != 0 or not result.is_file() or result.stat().st_size == 0:
            detail = (completed.stderr or completed.stdout or b"").decode(errors="replace")[-300:]
            raise RecalculationError(f"LibreOffice failed (exit {completed.returncode}): {detail}")
        return result.read_bytes()
