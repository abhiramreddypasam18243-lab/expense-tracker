"""Monthly summary, spending trend and budgets (spec sections 6.2 and 6.3).

Spend is reported as a POSITIVE number of paise (₹500 spent -> 50000). Transfers are
excluded everywhere; refunds are income (decision Q3).
"""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from expenses import db

WARNING_PERCENT = 80


@dataclass
class CategoryTotal:
    name: str
    color: str
    spend_minor: int


@dataclass
class MonthlySummary:
    month: str
    income_minor: int = 0
    spend_minor: int = 0
    net_minor: int = 0
    txn_count: int = 0
    by_category: list[CategoryTotal] = field(default_factory=list)
    top_merchants: list[tuple[str, int]] = field(default_factory=list)


@dataclass
class BudgetStatus:
    category_id: int
    name: str
    color: str
    limit_minor: int
    spent_minor: int
    status: str  # 'ok' | 'warning' | 'over'

    @property
    def percent(self) -> int:
        """Whole percent used, for progress bars (display only)."""
        return self.spent_minor * 100 // self.limit_minor


# --- month helpers --------------------------------------------------------------------------


def parse_month(month: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{4})-(\d{2})", month)
    if not match or not 1 <= int(match.group(2)) <= 12:
        raise ValueError("month must look like YYYY-MM")
    return int(match.group(1)), int(match.group(2))


def shift_month(month: str, delta: int) -> str:
    year, mon = parse_month(month)
    total = year * 12 + (mon - 1) + delta
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def month_range(month: str) -> tuple[date, date]:
    """'2026-03' -> (2026-03-01, 2026-04-01): start inclusive, end exclusive."""
    year, mon = parse_month(month)
    next_year, next_mon = parse_month(shift_month(month, 1))
    return date(year, mon, 1), date(next_year, next_mon, 1)


def latest_month(conn: sqlite3.Connection) -> str | None:
    """Most recent month with data, for sensible UI defaults."""
    rows = db.transactions_between(conn, date(1900, 1, 1), date(9999, 1, 1))
    return rows[-1]["txn_date"][:7] if rows else None


# --- 6.2 summary and trend ---------------------------------------------------------------------


def monthly_summary(conn: sqlite3.Connection, month: str, top_n: int = 5) -> MonthlySummary:
    start, end = month_range(month)
    summary = MonthlySummary(month=month)
    by_category: dict[str, int] = defaultdict(int)
    colors: dict[str, str] = {}
    by_merchant: dict[str, int] = defaultdict(int)

    for row in db.transactions_between(conn, start, end):
        summary.txn_count += 1  # every row, transfers included
        if row["category_kind"] == "transfer":
            continue
        amount = row["amount_minor"]
        if amount >= 0:
            summary.income_minor += amount
            continue
        spend = -amount
        summary.spend_minor += spend
        by_category[row["category_name"]] += spend
        colors[row["category_name"]] = row["category_color"]
        if row["merchant"]:
            by_merchant[row["merchant"]] += spend

    summary.net_minor = summary.income_minor - summary.spend_minor
    summary.by_category = [
        CategoryTotal(name, colors[name], total)
        for name, total in sorted(by_category.items(), key=lambda item: (-item[1], item[0]))
    ]
    summary.top_merchants = sorted(by_merchant.items(), key=lambda item: (-item[1], item[0]))[:top_n]
    return summary


def spending_trend(conn: sqlite3.Connection, end_month: str, months: int = 12) -> list[tuple[str, int]]:
    """[(YYYY-MM, spend)] for `months` months ending at `end_month`; empty months are 0."""
    first = shift_month(end_month, -(months - 1))
    start, _ = month_range(first)
    _, end = month_range(end_month)
    totals = {shift_month(first, i): 0 for i in range(months)}
    for row in db.transactions_between(conn, start, end):
        if row["amount_minor"] < 0 and row["category_kind"] != "transfer":
            totals[row["txn_date"][:7]] -= row["amount_minor"]
    return list(totals.items())


# --- 6.3 budgets -------------------------------------------------------------------------------------


def budget_state(spent_minor: int, limit_minor: int) -> str:
    """ok < 80% <= warning <= 100% < over. Integer maths, so no rounding at the boundaries."""
    if spent_minor * 100 < limit_minor * WARNING_PERCENT:
        return "ok"
    if spent_minor <= limit_minor:
        return "warning"
    return "over"


def set_budget(conn: sqlite3.Connection, category_id: int, limit_minor: int) -> None:
    category = db.get_category(conn, category_id)
    if category is None or category["kind"] != "expense":
        raise ValueError("budgets are only for expense categories")
    if limit_minor <= 0:
        raise ValueError("budget must be greater than zero")
    with conn:
        db.upsert_budget(conn, category_id, limit_minor)


def remove_budget(conn: sqlite3.Connection, category_id: int) -> None:
    with conn:
        db.delete_budget(conn, category_id)


def budget_status(conn: sqlite3.Connection, month: str) -> list[BudgetStatus]:
    spent = {c.name: c.spend_minor for c in monthly_summary(conn, month).by_category}
    return [
        BudgetStatus(
            category_id=b["category_id"],
            name=b["name"],
            color=b["color"],
            limit_minor=b["monthly_limit_minor"],
            spent_minor=spent.get(b["name"], 0),
            status=budget_state(spent.get(b["name"], 0), b["monthly_limit_minor"]),
        )
        for b in db.list_budgets(conn)
    ]
