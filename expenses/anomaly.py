"""Unusually large spends per category: modified z-score (spec section 6.1).

M = 0.6745 * (x - median) / MAD            (Iglewicz & Hoaglin)
If MAD == 0:  M = (x - median) / (1.253314 * MeanAD)    (decision Q1)
Flag debits with M > 3.5 in categories with at least 8 debits, whole history (Q2).
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from expenses import db

THRESHOLD = 3.5
MIN_COUNT = 8
MAD_CONSTANT = Decimal("0.6745")
MEANAD_CONSTANT = Decimal("1.253314")


@dataclass
class Anomaly:
    transaction_id: int
    category: str
    amount_minor: int  # signed, as stored (debits are negative)
    median_minor: Decimal  # typical spend in this category (positive; may end in .5)
    score: float
    txn_date: str = ""
    merchant: str | None = None

    @property
    def times_typical(self) -> Decimal:
        """How many times the typical spend this was, e.g. 18.7 or 420 (display only)."""
        ratio = (Decimal(-self.amount_minor) / self.median_minor).quantize(Decimal("0.1"))
        return ratio.quantize(Decimal(1)) if ratio == ratio.to_integral_value() else ratio


# ---------------------------------------------------------------------------
# [LEARN] 6.1 — your turn. Make the AC-30 and AC-32 tests pass:
#     pytest -q tests/test_step5_insights.py
# ---------------------------------------------------------------------------


def median(values: Sequence[int]) -> Decimal:
    """Exact median as a Decimal. [1, 2] -> Decimal('1.5'). Empty input -> ValueError."""
    if not values:
        raise ValueError("median of an empty list")
    ordered = sorted(Decimal(v) for v in values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def robust_scores(values: Sequence[int]) -> list[float]:
    """Signed modified z-score for every value, in input order.

    Do the maths in Decimal; convert each final score to float.
    MAD == 0 -> mean-AD fallback; MeanAD == 0 too (all equal) -> every score 0.0.

    Example: [100, 110, 120, 130, 140, 150, 160, 5000] -> median 135, MAD 20,
             score(5000) = 0.6745 * 4865 / 20 = 164.07
    """
    centre = median(values)
    deviations = [Decimal(v) - centre for v in values]

    mad = median([abs(d) for d in deviations])
    if mad != 0:
        return [float(MAD_CONSTANT * d / mad) for d in deviations]

    # Decision Q1: more than half the values equal the median, so MAD is 0.
    mean_ad = sum(abs(d) for d in deviations) / len(deviations)
    if mean_ad == 0:
        return [0.0] * len(values)  # every value identical: nothing is unusual
    return [float(d / (MEANAD_CONSTANT * mean_ad)) for d in deviations]


# ---------------------------------------------------------------------------


def find_anomalies(
    conn: sqlite3.Connection, threshold: float = THRESHOLD, min_count: int = MIN_COUNT
) -> list[Anomaly]:
    """Debits with score > threshold, grouped by category, highest score first."""
    groups: dict[str, list] = defaultdict(list)
    for row in db.non_transfer_debits(conn):
        groups[row["category_name"]].append(row)

    found: list[Anomaly] = []
    for category, rows in groups.items():
        if len(rows) < min_count:
            continue
        values = [-row["amount_minor"] for row in rows]  # x = abs(debit)
        typical = median(values)
        for row, score in zip(rows, robust_scores(values), strict=True):
            if score > threshold:
                found.append(
                    Anomaly(
                        row["id"],
                        category,
                        row["amount_minor"],
                        typical,
                        score,
                        row["txn_date"],
                        row["merchant"],
                    )
                )
    found.sort(key=lambda a: a.score, reverse=True)
    return found
