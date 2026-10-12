"""Money parsing and display (spec rule I1).

Money is ALWAYS a signed int of paise inside the program (debit < 0, credit > 0).
Decimal is used only while parsing text; float is never used.
"""

from __future__ import annotations

import re
from decimal import Decimal

# Allowed number shapes AFTER sign and currency marker are removed.
# The integer part is one of:
#   plain     1234567
#   Indian    12,34,567   (last group 3 digits, groups of 2 before it)
#   Western   1,234,567   (groups of 3)
# followed by an optional fractional part (decimal count is checked separately
# so "10.555" gets its own clear error).
_NUMBER = re.compile(
    r"""^(
        \d+                       # plain
      | \d{1,2}(,\d{2})*,\d{3}    # Indian grouping
      | \d{1,3}(,\d{3})+          # Western grouping
    )(\.\d+)?$""",
    re.VERBOSE,
)
_CURRENCY = re.compile(r"^(₹|rs\.?|inr)\s*", re.IGNORECASE)
_DR_CR = re.compile(r"\s*(dr|cr)$", re.IGNORECASE)


class MoneyParseError(ValueError):
    """Raised for text that is not a valid amount. Never contains the raw text."""


def format_inr(minor: int) -> str:
    """Paise -> Indian-grouped rupees for display, e.g. 12345678 -> '₹1,23,456.78'."""
    sign = "-" if minor < 0 else ""
    rupees, paise = divmod(abs(minor), 100)
    digits = str(rupees)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        pairs = []
        while len(head) > 2:
            pairs.insert(0, head[-2:])
            head = head[:-2]
        pairs.insert(0, head)
        digits = ",".join(pairs) + "," + tail
    return f"{sign}₹{digits}.{paise:02d}"


def to_plain(minor: int) -> str:
    """Paise -> plain machine-readable decimal for CSV export: -123450 -> '-1234.50'."""
    sign = "-" if minor < 0 else ""
    rupees, paise = divmod(abs(minor), 100)
    return f"{sign}{rupees}.{paise:02d}"


def parse_amount(text: str) -> int:
    """Amount text -> signed paise. See SPEC section 4, rule I1.

    '1,23,456.78' -> 12345678, '(500)' -> -50000, '500 Dr' -> -50000, 'Rs.1234.5' -> 123450
    """
    if not isinstance(text, str):
        # Blocks floats (and ints) at the door: 10.5 may already be 10.4999... in binary.
        raise TypeError("parse_amount expects str")

    s = text.strip()
    sign = 1
    markers = 0  # how many sign markers we saw; more than one is ambiguous

    # 1. Trailing Dr / Cr (bank-statement style).
    match = _DR_CR.search(s)
    if match:
        markers += 1
        sign = -1 if match.group(1).lower() == "dr" else 1
        s = s[: match.start()].strip()

    # 2. Accounting parentheses: (500.00) means negative.
    if s.startswith("(") and s.endswith(")"):
        markers += 1
        sign = -1
        s = s[1:-1].strip()

    # 3. Leading + / -.
    if s[:1] in ("+", "-") and s:
        markers += 1
        sign = -1 if s[0] == "-" else 1
        s = s[1:].strip()

    if markers > 1:
        raise MoneyParseError("more than one sign marker")

    # 4. Currency marker: ₹, Rs, Rs., INR (any case, optional space).
    s = _CURRENCY.sub("", s)

    # 5. Validate the shape BEFORE Decimal: Decimal would accept '1e5', 'NaN', 'Infinity'.
    if not _NUMBER.match(s):
        raise MoneyParseError("not a valid amount")

    value = Decimal(s.replace(",", ""))
    if value.as_tuple().exponent < -2:
        raise MoneyParseError("more than 2 decimal places")

    # Exact: Decimal('1234.5') * 100 == Decimal('123450.0') -> int 123450. No rounding happens.
    return sign * int(value * 100)


def parse_debit_credit(debit: str, credit: str) -> int:
    """Separate Debit / Credit columns -> signed paise. Exactly one side must be filled.

    ('1,000.00', '') -> -100000, ('', '250') -> 25000
    """
    debit, credit = debit.strip(), credit.strip()
    if bool(debit) == bool(credit):
        raise MoneyParseError("exactly one of debit/credit must be filled")
    if debit:
        return -abs(parse_amount(debit))
    return abs(parse_amount(credit))
