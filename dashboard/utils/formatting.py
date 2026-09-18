"""European-format presentation helpers."""

from __future__ import annotations

from datetime import datetime, timezone


def as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def format_number(value, decimals: int = 2, *, sign: bool = False) -> str:
    number = as_float(value)
    prefix = "+" if sign and number > 0 else ""
    rendered = f"{abs(number) if number == 0 else number:,.{decimals}f}"
    return prefix + rendered.replace(",", "§").replace(".", ",").replace("§", ".")


def format_currency(value, decimals: int = 2, *, sign: bool = False) -> str:
    return f"{format_number(value, decimals, sign=sign)} €"


def format_percent(value, decimals: int = 2, *, sign: bool = False) -> str:
    return f"{format_number(as_float(value) * 100, decimals, sign=sign)} %"


def format_quantity(value, symbol: str = "") -> str:
    quantity = as_float(value)
    decimals = 8 if abs(quantity) < 1 else 4
    base = symbol.split("-")[0] if symbol else ""
    rendered = format_number(quantity, decimals).rstrip("0").rstrip(",")
    return f"{rendered} {base}".strip()


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def format_time(value: str | None) -> str:
    parsed = parse_timestamp(value)
    return parsed.astimezone().strftime("%H:%M:%S") if parsed else "—"


def format_elapsed(start: str | None, end: datetime | None = None) -> str:
    parsed = parse_timestamp(start)
    if parsed is None:
        return "—"
    end = end or datetime.now(timezone.utc)
    seconds = max(0, int((end - parsed.astimezone(timezone.utc)).total_seconds()))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
