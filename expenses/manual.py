"""Transactions added by hand: cash, a card, a wallet — anything not in a bank statement (spec I13).

Entries live in the same table as imported rows, inside one special batch (`db.manual_batch_id`)
that is not an import, so "Undo import" can never remove them. Only these entries can be edited or
deleted. Cash is a wallet: an ATM withdrawal is a transfer from the bank into it (category Cash,
kind transfer), and the cash purchases logged here are the spending.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date

from expenses import categorize, db, insights, learn
from expenses.importer import extract_merchant, normalize_description

log = logging.getLogger(__name__)

CASH_ACCOUNT = "Cash"
MAX_DESCRIPTION = 200
MAX_ACCOUNT = 60


class EntryError(ValueError):
    """The entry can't be saved; the message says what to fix."""


@dataclass(frozen=True)
class CashGap:
    withdrawn_minor: int  # ATM / cash withdrawals from imported statements
    logged_minor: int  # cash spending added by hand

    @property
    def unlogged_minor(self) -> int:
        return max(0, self.withdrawn_minor - self.logged_minor)


def _row(
    conn: sqlite3.Connection,
    txn_date: date,
    amount_minor: int,
    description: str,
    account: str,
    category_id: int | None,
    today: date | None,
) -> dict:
    """Validate and build the stored fields. category_id None = automatic (rules, then the model)."""
    description = " ".join((description or "").split())
    account = " ".join((account or "").split())
    if not isinstance(amount_minor, int) or isinstance(amount_minor, bool) or amount_minor == 0:
        raise EntryError("Enter an amount other than zero.")
    if not description:
        raise EntryError("Enter a description, e.g. “Tea at the canteen”.")
    if len(description) > MAX_DESCRIPTION:
        raise EntryError(f"Keep the description under {MAX_DESCRIPTION} characters.")
    if not account:
        raise EntryError("Say how it was paid, e.g. Cash.")
    if len(account) > MAX_ACCOUNT:
        raise EntryError(f"Keep the account name under {MAX_ACCOUNT} characters.")
    if txn_date > (today or date.today()):
        raise EntryError("The date can't be in the future.")
    norm = normalize_description(description)
    if category_id is None:
        categorize.seed_defaults(conn)
        category_id, source, confidence = categorize.classify(
            norm,
            amount_minor,
            db.list_rules(conn),
            categorize.uncategorised_id(conn),
            learn.predictor(conn),
        )
    else:
        if db.get_category(conn, category_id) is None:
            raise EntryError("That category no longer exists.")
        source, confidence = "manual", None
    return {
        "account": account,
        "txn_date": txn_date.isoformat(),
        "description": description,
        "description_norm": norm,
        "merchant": extract_merchant(norm),
        "amount_minor": amount_minor,
        "category_id": category_id,
        "category_source": source,
        "category_confidence": confidence,
    }


def add(
    conn: sqlite3.Connection,
    txn_date: date,
    amount_minor: int,
    description: str,
    account: str = CASH_ACCOUNT,
    category_id: int | None = None,
    today: date | None = None,
) -> int:
    """Store a new entry (negative amount = spent, positive = received). Returns its id."""
    row = _row(conn, txn_date, amount_minor, description, account, category_id, today)
    with conn:
        batch_id = db.manual_batch_id(conn)
        db.insert_transaction(
            conn,
            {
                **row,
                "orig_currency": "INR",
                "orig_amount_minor": amount_minor,
                "fx_rate": None,
                # Unique per entry: two identical ₹20 teas on one day are two real purchases.
                "fingerprint": f"manual:{uuid.uuid4().hex}",
                "batch_id": batch_id,
            },
        )
        txn_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    log.info("manual entry added id=%d", txn_id)  # id only — never description or amount
    return txn_id


def update(
    conn: sqlite3.Connection,
    txn_id: int,
    txn_date: date,
    amount_minor: int,
    description: str,
    account: str,
    category_id: int | None = None,
    today: date | None = None,
) -> None:
    row = _row(conn, txn_date, amount_minor, description, account, category_id, today)
    with conn:
        if not db.update_manual_transaction(conn, txn_id, row):
            raise EntryError("Only entries you added by hand can be edited.")
    log.info("manual entry updated id=%d", txn_id)


def delete(conn: sqlite3.Connection, txn_id: int) -> None:
    with conn:
        if not db.delete_manual_transaction(conn, txn_id):
            raise EntryError("Only entries you added by hand can be deleted.")
    log.info("manual entry deleted id=%d", txn_id)


def is_manual(conn: sqlite3.Connection, txn_id: int) -> bool:
    row = db.get_transaction(conn, txn_id)
    return bool(row and row["is_manual"])


def cash_gap(conn: sqlite3.Connection, month: str) -> CashGap:
    """Cash withdrawn in the month vs cash spending logged by hand in the same month."""
    start, end = insights.month_range(month)
    withdrawn, logged = db.cash_totals(conn, start, end, CASH_ACCOUNT)
    return CashGap(withdrawn, logged)


def account_choices(conn: sqlite3.Connection) -> list[str]:
    """Cash first, then every account already used (statements and earlier entries)."""
    return [CASH_ACCOUNT, *(a for a in db.list_accounts(conn) if a != CASH_ACCOUNT)]
