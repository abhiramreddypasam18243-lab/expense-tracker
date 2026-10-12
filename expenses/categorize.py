"""Keyword-rule categorisation (spec section 5).

Rules are tried in ascending priority (then id); the first rule whose pattern is a
substring of the normalised description wins. No match -> Uncategorised.
Manual choices (category_source = 'manual') are never overwritten by rules.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping

from expenses import db

UNCATEGORISED = "Uncategorised"

# (name, kind, colour). Colours are the UI palette for charts.
DEFAULT_CATEGORIES = [
    ("Food & Dining", "expense", "#F97316"),
    ("Groceries", "expense", "#22C55E"),
    ("Transport", "expense", "#3B82F6"),
    ("Shopping", "expense", "#EC4899"),
    ("Bills & Utilities", "expense", "#EAB308"),
    ("Rent", "expense", "#8B5CF6"),
    ("Entertainment", "expense", "#EF4444"),
    ("Health", "expense", "#14B8A6"),
    ("Education", "expense", "#6366F1"),
    ("Travel", "expense", "#0EA5E9"),
    ("Salary", "income", "#16A34A"),
    ("Refunds", "income", "#84CC16"),
    ("Transfers", "transfer", "#94A3B8"),
    ("Cash", "expense", "#A16207"),
    (UNCATEGORISED, "expense", "#9CA3AF"),
]

# (pattern, category, priority). Lower priority runs first, so specific
# rules (REFUND, SALARY, SELF TRANSFER) beat broad ones (AMAZON, RENT).
DEFAULT_RULES = [
    ("REFUND", "Refunds", 10),
    ("SALARY", "Salary", 10),
    ("SELF TRANSFER", "Transfers", 10),
    ("SWIGGY", "Food & Dining", 100),
    ("ZOMATO", "Food & Dining", 100),
    ("CHAI POINT", "Food & Dining", 100),
    ("STARBUCKS", "Food & Dining", 100),
    ("DOMINOS", "Food & Dining", 100),
    ("BIG BAZAAR", "Groceries", 100),
    ("BLINKIT", "Groceries", 100),
    ("ZEPTO", "Groceries", 100),
    ("BIGBASKET", "Groceries", 100),
    ("DMART", "Groceries", 100),
    ("UBER", "Transport", 100),
    ("OLA CABS", "Transport", 100),
    ("RAPIDO", "Transport", 100),
    ("METRO", "Transport", 100),
    ("FASTAG", "Transport", 100),
    ("AMAZON", "Shopping", 100),
    ("FLIPKART", "Shopping", 100),
    ("MYNTRA", "Shopping", 100),
    ("ELECTRICITY", "Bills & Utilities", 100),
    ("BESCOM", "Bills & Utilities", 100),
    ("JIO", "Bills & Utilities", 100),
    ("AIRTEL", "Bills & Utilities", 100),
    ("BROADBAND", "Bills & Utilities", 100),
    ("PVR", "Entertainment", 100),
    ("NETFLIX", "Entertainment", 100),
    ("SPOTIFY", "Entertainment", 100),
    ("BOOKMYSHOW", "Entertainment", 100),
    ("PHARMACY", "Health", 100),
    ("HOSPITAL", "Health", 100),
    ("COURSERA", "Education", 100),
    ("UDEMY", "Education", 100),
    ("IRCTC", "Travel", 100),
    ("MAKEMYTRIP", "Travel", 100),
    ("INDIGO", "Travel", 100),
    # Not plain "ATM": it is a substring of "TREATMENT".
    ("ATM WDL", "Cash", 100),
    ("ATM CASH", "Cash", 100),
    ("CASH WDL", "Cash", 100),
    ("RENT", "Rent", 200),  # broad (also inside "PARENT"), so it runs last
]


def seed_defaults(conn: sqlite3.Connection) -> None:
    """Create default categories and rules on a fresh database. No-op otherwise.

    "Fresh" means no categories at all, so categories the user deleted are not re-added.
    """
    with conn:
        if db.count_categories(conn) > 0:
            return
        ids = {name: db.insert_category(conn, name, kind, color) for name, kind, color in DEFAULT_CATEGORIES}
        for pattern, category, priority in DEFAULT_RULES:
            db.insert_rule(conn, pattern, ids[category], priority)


# --- lookups --------------------------------------------------------------------------------


def list_categories(conn: sqlite3.Connection):
    return db.list_categories(conn)


def list_rules(conn: sqlite3.Connection):
    return db.list_rules(conn)


def category_id(conn: sqlite3.Connection, name: str) -> int | None:
    return db.get_category_id(conn, name)


def uncategorised_id(conn: sqlite3.Connection) -> int:
    found = db.get_category_id(conn, UNCATEGORISED)
    if found is None:
        raise RuntimeError("Uncategorised category missing; call seed_defaults first")
    return found


# --- matching -------------------------------------------------------------------------------


def match_category(description_norm: str, rules: Iterable[Mapping]) -> int | None:
    """First matching rule's category_id, or None. Sorts defensively by (priority, id)."""
    for rule in sorted(rules, key=lambda r: (r["priority"], r["id"])):
        if rule["pattern"].upper() in description_norm:
            return rule["category_id"]
    return None


def categorize(description_norm: str, rules: Iterable[Mapping], fallback_id: int) -> tuple[int, str]:
    """-> (category_id, category_source) for a new row."""
    found = match_category(description_norm, rules)
    return (found, "rule") if found is not None else (fallback_id, "default")


def recategorize(conn: sqlite3.Connection) -> int:
    """Re-run rules on every non-manual row. Returns how many rows changed category."""
    rules = db.list_rules(conn)
    fallback = uncategorised_id(conn)
    changed = 0
    with conn:
        for row in db.list_non_manual_rows(conn):
            new_id, source = categorize(row["description_norm"], rules, fallback)
            if new_id != row["category_id"]:
                changed += 1
            db.set_transaction_category(conn, row["id"], new_id, source)
    return changed


def set_category(conn: sqlite3.Connection, txn_id: int, category_id: int) -> None:
    """Manual override by the user; survives recategorize()."""
    with conn:
        db.set_transaction_category(conn, txn_id, category_id, "manual")


# --- editing (Settings page) -------------------------------------------------------------------


def add_category(conn: sqlite3.Connection, name: str, kind: str = "expense", color: str = "#64748B") -> int:
    name = name.strip()
    if not name:
        raise ValueError("category name is required")
    with conn:
        return db.insert_category(conn, name, kind, color)


def update_category(conn: sqlite3.Connection, category_id: int, name: str, kind: str, color: str) -> None:
    if category_id == uncategorised_id(conn) and name != UNCATEGORISED:
        raise ValueError("Uncategorised cannot be renamed")
    with conn:
        db.update_category(conn, category_id, name.strip(), kind, color)


def delete_category(conn: sqlite3.Connection, category_id: int) -> None:
    """Delete a category; its transactions move to Uncategorised, its rules and budget go too."""
    fallback = uncategorised_id(conn)
    if category_id == fallback:
        raise ValueError("Uncategorised cannot be deleted")
    with conn:
        db.delete_category(conn, category_id, fallback)


def add_rule(conn: sqlite3.Connection, pattern: str, category_id: int, priority: int = 100) -> int:
    pattern = " ".join(pattern.split()).upper()  # same normalisation as descriptions
    if not pattern:
        raise ValueError("rule pattern is required")
    with conn:
        return db.insert_rule(conn, pattern, category_id, priority)


def update_rule(
    conn: sqlite3.Connection, rule_id: int, pattern: str, category_id: int, priority: int
) -> None:
    pattern = " ".join(pattern.split()).upper()
    if not pattern:
        raise ValueError("rule pattern is required")
    with conn:
        db.update_rule(conn, rule_id, pattern, category_id, priority)


def delete_rule(conn: sqlite3.Connection, rule_id: int) -> None:
    with conn:
        db.delete_rule(conn, rule_id)
