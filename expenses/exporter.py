"""CSV export (spec section 7.1). The output re-imports cleanly: same amounts, same rows."""

from __future__ import annotations

import csv
import io
import sqlite3

from expenses import db
from expenses.insights import month_range
from expenses.money import to_plain

HEADER = ["Date", "Description", "Amount", "Category", "Merchant", "Account"]


def export_csv(
    conn: sqlite3.Connection,
    month: str | None = None,
    category: str | None = None,
    account: str | None = None,
    search: str | None = None,
) -> str:
    """Transactions (oldest first) as CSV text. Amounts are plain INR decimals, e.g. -1234.50."""
    start, end = month_range(month) if month else (None, None)
    rows = db.list_transactions(
        conn, start=start, end=end, category=category, account=account, search=search, newest_first=False
    )
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(HEADER)
    for row in rows:
        writer.writerow(
            [
                row["txn_date"],
                row["description"],
                to_plain(row["amount_minor"]),
                row["category_name"],
                row["merchant"] or "",
                row["account"],
            ]
        )
    return buf.getvalue()
