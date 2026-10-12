"""Recurring monthly payments: subscriptions and fixed bills (spec section 6.4).

A group of debits is recurring when it has >= 3 payments in >= 3 months, every gap is
20-40 days, and every amount is within ±10 % of the group's median.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from expenses import db
from expenses.anomaly import median
from expenses.importer import display_name

MIN_PAYMENTS = 3
MIN_GAP_DAYS, MAX_GAP_DAYS = 20, 40
TOLERANCE_PERCENT = 10
ACTIVE_WITHIN_DAYS = 40


@dataclass
class Recurring:
    name: str
    category: str
    typical_minor: Decimal  # median payment, positive paise (may end in .5)
    count: int
    last_date: date
    interval_days: int  # median gap, rounded half up
    next_date: date
    active: bool


def recurring_key(description_norm: str, merchant: str | None) -> str:
    """Merchant if known, else the description without words that contain digits (references)."""
    if merchant:
        return merchant.upper()
    return " ".join(word for word in description_norm.split() if not any(ch.isdigit() for ch in word))


def _is_monthly(dates: list[date]) -> bool:
    gaps = [(b - a).days for a, b in zip(dates, dates[1:], strict=False)]
    return all(MIN_GAP_DAYS <= gap <= MAX_GAP_DAYS for gap in gaps)


def _amounts_stable(amounts: list[int], typical: Decimal) -> bool:
    return all(abs(x - typical) * 100 <= typical * TOLERANCE_PERCENT for x in amounts)


def find_recurring(conn: sqlite3.Connection) -> list[Recurring]:
    rows = db.non_transfer_debits(conn)
    if not rows:
        return []
    newest = date.fromisoformat(db.list_transactions(conn, limit=1)[0]["txn_date"])  # newest row of any kind

    groups: dict[str, list] = defaultdict(list)
    for row in rows:
        groups[recurring_key(row["description_norm"], row["merchant"])].append(row)

    found: list[Recurring] = []
    for key, group in groups.items():
        group.sort(key=lambda r: (r["txn_date"], r["id"]))
        dates = [date.fromisoformat(r["txn_date"]) for r in group]
        amounts = [-r["amount_minor"] for r in group]
        if len(group) < MIN_PAYMENTS or len({d.strftime("%Y-%m") for d in dates}) < MIN_PAYMENTS:
            continue
        typical = median(amounts)
        if not _is_monthly(dates) or not _amounts_stable(amounts, typical):
            continue
        gaps = [(b - a).days for a, b in zip(dates, dates[1:], strict=False)]
        interval = int(median(gaps).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        found.append(
            Recurring(
                name=display_name(key),
                category=group[-1]["category_name"],
                typical_minor=typical,
                count=len(group),
                last_date=dates[-1],
                interval_days=interval,
                next_date=dates[-1] + timedelta(days=interval),
                active=(newest - dates[-1]).days <= ACTIVE_WITHIN_DAYS,
            )
        )
    found.sort(key=lambda r: (-r.typical_minor, r.name))
    return found
