"""Persian text helpers: digits, numbers, durations and money."""

_TO_ASCII = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_TO_PERSIAN = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def normalize_digits(text: str) -> str:
    """Persian and Arabic-Indic digits → ASCII."""
    return text.translate(_TO_ASCII)


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
