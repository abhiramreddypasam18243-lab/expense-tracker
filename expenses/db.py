"""SQLite storage: schema and small repository functions (spec section 3).

All SQL lives in this module. Money columns are INTEGER paise; exchange rates are
stored as TEXT so the exact Decimal survives the round trip.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

DEFAULT_DB_PATH = "data/expenses.db"
SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    id    INTEGER PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE,
    kind  TEXT NOT NULL CHECK (kind IN ('expense', 'income', 'transfer')),
    color TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rules (
    id          INTEGER PRIMARY KEY,
    pattern     TEXT NOT NULL,
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    priority    INTEGER NOT NULL DEFAULT 100
);

CREATE TABLE IF NOT EXISTS budgets (
    category_id         INTEGER PRIMARY KEY REFERENCES categories(id) ON DELETE CASCADE,
    monthly_limit_minor INTEGER NOT NULL CHECK (monthly_limit_minor > 0)
);

CREATE TABLE IF NOT EXISTS import_batches (
    id             INTEGER PRIMARY KEY,
    source_name    TEXT NOT NULL,
    account        TEXT NOT NULL,
    imported_at    TEXT NOT NULL,
    rows_total     INTEGER NOT NULL DEFAULT 0,
    rows_imported  INTEGER NOT NULL DEFAULT 0,
    rows_duplicate INTEGER NOT NULL DEFAULT 0,
    rows_rejected  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS transactions (
    id                INTEGER PRIMARY KEY,
    account           TEXT NOT NULL,
    txn_date          TEXT NOT NULL,
    description       TEXT NOT NULL,
    description_norm  TEXT NOT NULL,
    merchant          TEXT,
    amount_minor      INTEGER NOT NULL,
    orig_currency     TEXT NOT NULL DEFAULT 'INR',
    orig_amount_minor INTEGER NOT NULL,
    fx_rate           TEXT,
    category_id       INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    category_source   TEXT NOT NULL DEFAULT 'default'
                      CHECK (category_source IN ('rule', 'manual', 'default')),
    fingerprint       TEXT NOT NULL UNIQUE,
    batch_id          INTEGER NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_transactions_date ON transactions(txn_date);
CREATE INDEX IF NOT EXISTS ix_transactions_batch ON transactions(batch_id);

CREATE TABLE IF NOT EXISTS fx_rates (
    currency    TEXT NOT NULL,
    rate_date   TEXT NOT NULL,
    rate        TEXT NOT NULL,
    source_date TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    PRIMARY KEY (currency, rate_date)
);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect(path: str | os.PathLike | None = None) -> sqlite3.Connection:
    """Open (and if needed create) the database. ':memory:' is allowed for tests."""
    path = str(path or os.environ.get("EXPENSES_DB", DEFAULT_DB_PATH))
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return conn


# --- import batches --------------------------------------------------------------


def create_batch(conn: sqlite3.Connection, source_name: str, account: str) -> int:
    cur = conn.execute(
        "INSERT INTO import_batches (source_name, account, imported_at) VALUES (?, ?, ?)",
        (source_name, account, _now()),
    )
    return cur.lastrowid


def finish_batch(
    conn: sqlite3.Connection, batch_id: int, total: int, imported: int, duplicate: int, rejected: int
) -> None:
    conn.execute(
        "UPDATE import_batches SET rows_total = ?, rows_imported = ?, rows_duplicate = ?, rows_rejected = ?"
        " WHERE id = ?",
        (total, imported, duplicate, rejected, batch_id),
    )


def delete_batch(conn: sqlite3.Connection, batch_id: int) -> int:
    """Delete a batch and (via ON DELETE CASCADE) its transactions. Returns rows removed."""
    with conn:
        count = conn.execute("SELECT COUNT(*) FROM transactions WHERE batch_id = ?", (batch_id,)).fetchone()[
            0
        ]
        conn.execute("DELETE FROM import_batches WHERE id = ?", (batch_id,))
    return count


def list_batches(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM import_batches ORDER BY id DESC").fetchall()


# --- transactions --------------------------------------------------------------------


def insert_transaction(conn: sqlite3.Connection, row: dict) -> bool:
    """Insert one transaction. Returns False if its fingerprint already exists (duplicate)."""
    cur = conn.execute(
        """INSERT OR IGNORE INTO transactions
           (account, txn_date, description, description_norm, merchant, amount_minor,
            orig_currency, orig_amount_minor, fx_rate, category_id, category_source,
            fingerprint, batch_id, created_at)
           VALUES (:account, :txn_date, :description, :description_norm, :merchant, :amount_minor,
                   :orig_currency, :orig_amount_minor, :fx_rate, :category_id, :category_source,
                   :fingerprint, :batch_id, :created_at)""",
        {"category_id": None, "category_source": "default", "created_at": _now(), **row},
    )
    return cur.rowcount == 1


# --- exchange-rate cache -----------------------------------------------------------------


def get_fx_rate(conn: sqlite3.Connection, currency: str, on: date) -> Decimal | None:
    row = conn.execute(
        "SELECT rate FROM fx_rates WHERE currency = ? AND rate_date = ?", (currency, on.isoformat())
    ).fetchone()
    return Decimal(row["rate"]) if row else None


def put_fx_rate(conn: sqlite3.Connection, currency: str, on: date, rate: Decimal, source_date: date) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO fx_rates (currency, rate_date, rate, source_date, fetched_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (currency, on.isoformat(), str(rate), source_date.isoformat(), _now()),
    )
