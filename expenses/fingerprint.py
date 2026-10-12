"""Transaction fingerprint for de-duplication (spec rule I5).

The database has a UNIQUE constraint on the fingerprint, so a row whose
fingerprint already exists is a duplicate and is skipped on import.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import date

RowKey = tuple[str, date, str, int, str]


def fingerprint(
    account: str,
    txn_date: date,
    orig_currency: str,
    orig_amount_minor: int,
    description_norm: str,
    occurrence: int,
) -> str:
    """sha256 hex of 'account|date|currency|amount|description_norm|occurrence'. See SPEC I5.

    Example: ('HDFC', 2026-01-05, 'INR', -2000, 'POS 0042 CHAI POINT', 1)
             -> sha256('HDFC|2026-01-05|INR|-2000|POS 0042 CHAI POINT|1')
    The original currency/amount are used (not converted INR) so a re-import always matches.
    """
    parts = (account, txn_date.isoformat(), orig_currency, orig_amount_minor, description_norm, occurrence)
    raw = "|".join(str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class OccurrenceCounter:
    """Numbers identical rows inside ONE file: 0 for the first, 1 for the second, ...

    Create one counter per imported file. Two genuine ₹20 teas on the same day get
    occurrence 0 and 1, so their fingerprints differ and both are stored. Because the
    numbering restarts in every file, the same two teas in an overlapping statement get
    0 and 1 again and are recognised as duplicates.
    """

    def __init__(self) -> None:
        self._seen: Counter[RowKey] = Counter()

    def next(
        self, account: str, txn_date: date, orig_currency: str, orig_amount_minor: int, description_norm: str
    ) -> int:
        key = (account, txn_date, orig_currency, orig_amount_minor, description_norm)
        occurrence = self._seen[key]
        self._seen[key] += 1
        return occurrence
