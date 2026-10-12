"""Currency conversion (spec rule I8).

This is the ONLY module allowed to use the network. It sends a currency code and a
date — never an amount, description, merchant or account name.
"""

from __future__ import annotations

import json
import re
import urllib.request
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

RATE_URL = "https://api.frankfurter.dev/v1/{on}?from={currency}&to=INR"
TIMEOUT_SECONDS = 5
_CURRENCY_CODE = re.compile(r"^[A-Z]{3}$")


class FxError(Exception):
    """Rate could not be fetched or understood."""


def parse_rate_response(body: bytes | str) -> tuple[Decimal, date]:
    """Frankfurter JSON -> (INR per 1 unit, date the provider used).

    parse_float=Decimal makes json build Decimal('90.3') directly from the text,
    so the rate is never a float, not even for a moment.
    """
    try:
        data = json.loads(body, parse_float=Decimal, parse_int=Decimal)
        rate = data["rates"]["INR"]
        source_date = date.fromisoformat(data["date"])
    except (ValueError, KeyError, TypeError, InvalidOperation) as exc:
        raise FxError("unexpected rate response") from exc
    if not isinstance(rate, Decimal) or not rate.is_finite() or rate <= 0:
        raise FxError("unexpected rate value")
    return rate, source_date


def fetch_rate(currency: str, on: date) -> tuple[Decimal, date]:
    """Live ECB reference rate for `currency` -> INR on date `on` (network call)."""
    if not _CURRENCY_CODE.match(currency):
        raise FxError("invalid currency code")
    url = RATE_URL.format(on=on.isoformat(), currency=currency)
    request = urllib.request.Request(url, headers={"User-Agent": "expense-tracker"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read()
    except OSError as exc:  # URLError, HTTPError and timeouts are all OSError subclasses
        raise FxError("rate service unreachable") from exc
    return parse_rate_response(body)


def convert(orig_minor: int, rate: Decimal) -> int:
    """Foreign hundredths x rate -> INR paise, rounded half-to-even.

    This is the only place money is ever rounded. Example: 1234 cents x 90.3 = 111430.2 -> 111430.
    """
    return int((Decimal(orig_minor) * rate).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))
