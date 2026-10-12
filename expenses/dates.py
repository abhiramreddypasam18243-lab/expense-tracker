"""Statement date parsing (spec rule I2). Ambiguous dates are always day-first."""

from __future__ import annotations

from datetime import date, datetime

# Order matters only for readability: every format is unambiguous given its separator,
# and none of them is month-first, so "03/04/2026" can only mean 3 April.
_FORMATS = (
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%Y-%m-%d",
    "%d-%b-%Y",
    "%d %b %Y",
    "%d/%m/%y",  # %Y needs 4 digits, so two-digit years only reach this one (YY -> 20YY)
)


class DateParseError(ValueError):
    """Raised for unparseable or impossible dates. Never contains the raw text."""


def parse_date(text: str) -> date:
    if not isinstance(text, str):
        raise TypeError("parse_date expects str")
    cleaned = text.strip()
    for fmt in _FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    raise DateParseError("unrecognised or impossible date")
