"""Persian text helpers: digits, numbers, durations and money."""

_TO_ASCII = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_TO_PERSIAN = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def normalize_digits(text: str) -> str:
    """Persian and Arabic-Indic digits → ASCII."""
    return text.translate(_TO_ASCII)


def normalize_employee_code(text: str) -> str | None:
    """A typed or uploaded staff number in one form, or None if it can't be one.

    Mobile numbers are written many ways (۰۹۱۲…, +98 912…, 0098912…, 912 …); all become 09xxxxxxxxx.
    Anything else that is only digits (a personnel code) is kept as it is.
    """
    digits = normalize_digits(str(text)).strip()
    if digits.endswith(".0") and digits[:-2].isdigit():  # a number cell read as a float
        digits = digits[:-2]
    for ch in " -_()+.\u200c\u200f\u200e":
        digits = digits.replace(ch, "")
    if not digits.isdigit() or not 3 <= len(digits) <= 20:
        return None
    for prefix in ("0098", "98"):
        if digits.startswith(prefix + "9") and len(digits) == len(prefix) + 10:
            return "0" + digits[len(prefix) :]
    if digits.startswith("9") and len(digits) == 10:
        return "0" + digits
    return digits


def to_persian_digits(value: object) -> str:
    return str(value).translate(_TO_PERSIAN)


def format_number(value: int) -> str:
    """12500 → '۱۲٬۵۰۰'"""
    return to_persian_digits(f"{value:,}".replace(",", "٬"))


def format_money(value: int, currency_label: str) -> str:
    return f"{format_number(value)} {currency_label}"


def format_minutes(minutes: int | None, signed: bool = False) -> str:
    """125 → '۲:۰۵'. With signed=True, negative values get a leading minus and positives a plus."""
    if minutes is None:
        return "—"
    sign = ""
    if minutes < 0:
        sign = "-"
    elif signed and minutes > 0:
        sign = "+"
    value = abs(minutes)
    return to_persian_digits(f"{sign}{value // 60}:{value % 60:02d}")


def parse_int(text: str) -> int | None:
    cleaned = normalize_digits(text.strip())
    if not cleaned.isdigit():
        return None
    return int(cleaned)


def clean_text(text: str | None, max_length: int) -> str:
    """Collapse whitespace and trim to a maximum length."""
    if not text:
        return ""
    return " ".join(text.split())[:max_length]
