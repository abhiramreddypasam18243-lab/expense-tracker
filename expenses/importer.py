"""CSV statement import pipeline (spec rules I3, I4, I6, I7, I8).

read CSV -> find header -> per row: parse date + money -> normalise -> convert currency
-> fingerprint -> insert (duplicates are skipped by the UNIQUE fingerprint).
"""

from __future__ import annotations

import csv
import io
import logging
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from expenses import categorize, db, fx
from expenses.dates import DateParseError, parse_date
from expenses.fingerprint import OccurrenceCounter, fingerprint
from expenses.money import MoneyParseError, parse_amount, parse_debit_credit

log = logging.getLogger(__name__)

HEADER_SEARCH_ROWS = 20

# Header aliases, already in "squashed" form (lower case, no spaces / dots / underscores).
_ALIASES = {
    "date": {"date", "txndate", "transactiondate", "valuedate", "postingdate"},
    "description": {"description", "narration", "particulars", "details", "remarks"},
    "amount": {"amount", "txnamount", "transactionamount"},
    "debit": {"debit", "withdrawal", "withdrawalamt", "dr"},
    "credit": {"credit", "deposit", "depositamt", "cr"},
    "currency": {"currency", "ccy", "curr", "currencycode"},
}

FetchRate = Callable[[str, date], "tuple[Decimal, date] | None"]


class HeaderNotFoundError(ValueError):
    """No usable header row in the first HEADER_SEARCH_ROWS rows."""


@dataclass
class ColumnMap:
    date: int
    description: int
    amount: int | None = None
    debit: int | None = None
    credit: int | None = None
    currency: int | None = None


@dataclass
class ImportReport:
    batch_id: int
    rows_total: int = 0
    rows_imported: int = 0
    rows_duplicate: int = 0
    rows_rejected: int = 0
    errors: list[tuple[int, str]] = field(default_factory=list)


class _RowRejected(Exception):
    """Internal: this row is invalid; the message is the (value-free) reason."""


# --- I3 header detection ------------------------------------------------------------


def _squash(cell: str) -> str:
    return re.sub(r"[\s._]+", "", cell).lower()


def find_header(rows: list[list[str]]) -> tuple[int, ColumnMap]:
    """Return (row index, column map) of the first valid header row."""
    for index, row in enumerate(rows[:HEADER_SEARCH_ROWS]):
        found: dict[str, int] = {}
        for col, cell in enumerate(row):
            key = _squash(cell)
            for name, aliases in _ALIASES.items():
                if key in aliases and name not in found:
                    found[name] = col
        has_amount = "amount" in found
        has_pair = "debit" in found and "credit" in found
        if "date" in found and "description" in found and (has_amount or has_pair):
            cols = ColumnMap(
                date=found["date"], description=found["description"], currency=found.get("currency")
            )
            if has_amount:  # amount wins over a debit/credit pair
                cols.amount = found["amount"]
            else:
                cols.debit, cols.credit = found["debit"], found["credit"]
            return index, cols
    raise HeaderNotFoundError(
        f"no header with date, description and amount columns in the first {HEADER_SEARCH_ROWS} lines"
    )


# --- I4 normalisation and merchant ---------------------------------------------------------

_UPI_SLASH = re.compile(r"^UPI/[^/]+/([^/]+)")
_UPI_DASH = re.compile(r"^UPI-([^-]+)-")
_POS = re.compile(r"^POS \d+ (.+)$")


def normalize_description(text: str) -> str:
    """Upper case, whitespace runs collapsed, trimmed."""
    return " ".join(text.split()).upper()


def display_name(text: str) -> str:
    """'PVR CINEMAS' -> 'PVR Cinemas', 'SWIGGY' -> 'Swiggy'.

    Words with no vowels or with digits stay upper case: they are usually acronyms or codes.
    """
    words = []
    for word in text.split():
        keep = not re.search(r"[AEIOU]", word.upper()) or re.search(r"\d", word)
        words.append(word.upper() if keep else word.capitalize())
    return " ".join(words)


def extract_merchant(description_norm: str) -> str | None:
    """Best-effort merchant from UPI / POS descriptions, for display; None otherwise."""
    for pattern in (_UPI_SLASH, _UPI_DASH, _POS):
        match = pattern.match(description_norm)
        if match:
            name = match.group(1).strip()
            return display_name(name) if name else None
    return None


# --- I8 currency -----------------------------------------------------------------------------


class RateLookup:
    """DB cache in front of the live fetch. Failures are remembered for this import only."""

    def __init__(self, conn: sqlite3.Connection, fetch_rate: FetchRate) -> None:
        self.conn = conn
        self.fetch_rate = fetch_rate
        self.failed: set[tuple[str, date]] = set()

    def __call__(self, currency: str, on: date) -> Decimal | None:
        cached = db.get_fx_rate(self.conn, currency, on)
        if cached is not None:
            return cached
        if (currency, on) in self.failed:
            return None
        try:
            result = self.fetch_rate(currency, on)
        except fx.FxError:
            result = None
        if result is None:
            self.failed.add((currency, on))
            return None
        rate, source_date = result
        db.put_fx_rate(self.conn, currency, on, rate, source_date)
        return rate


# --- I1/I2/I8 per-row parsing -------------------------------------------------------------------


def _cell(row: list[str], index: int | None) -> str:
    if index is None:
        return ""
    if index >= len(row):
        raise _RowRejected("missing columns")
    return row[index]


def _parse_row(row: list[str], cols: ColumnMap, rates: RateLookup) -> dict:
    try:
        txn_date = parse_date(_cell(row, cols.date))
    except DateParseError:
        raise _RowRejected("invalid date") from None

    description = _cell(row, cols.description).strip()
    if not description:
        raise _RowRejected("empty description")

    try:
        if cols.amount is not None:
            orig_amount = parse_amount(_cell(row, cols.amount))
        else:
            orig_amount = parse_debit_credit(_cell(row, cols.debit), _cell(row, cols.credit))
    except MoneyParseError:
        raise _RowRejected("invalid amount") from None

    currency = _cell(row, cols.currency).strip().upper() or "INR"
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise _RowRejected("invalid currency code")

    fx_rate = None
    amount = orig_amount
    if currency != "INR":
        fx_rate = rates(currency, txn_date)
        if fx_rate is None:
            raise _RowRejected("exchange rate unavailable")
        amount = fx.convert(orig_amount, fx_rate)

    description_norm = normalize_description(description)
    return {
        "txn_date": txn_date,
        "description": description,
        "description_norm": description_norm,
        "merchant": extract_merchant(description_norm),
        "amount_minor": amount,
        "orig_currency": currency,
        "orig_amount_minor": orig_amount,
        "fx_rate": None if fx_rate is None else str(fx_rate),
    }


# --- I6/I7 import -------------------------------------------------------------------------------


@dataclass
class PreparedFile:
    """A parsed statement, ready to insert. Nothing has been written to the database yet."""

    account: str
    source_name: str
    rows_total: int = 0
    rows: list[dict] = field(default_factory=list)  # insert-ready rows (they also carry "line")
    errors: list[tuple[int, str]] = field(default_factory=list)


def base_name(source_name: str) -> str:
    """File name only, never the folder path: 'C:/x/hdfc.csv' -> 'hdfc.csv'."""
    return source_name.replace("\\", "/").split("/")[-1]


def prepare_file(
    conn: sqlite3.Connection,
    text: str,
    account: str,
    source_name: str,
    rates: RateLookup,
    rules: list,
    fallback_category: int,
) -> PreparedFile:
    """Parse, normalise, fingerprint and categorise one statement (I1-I5, I8). No database writes,
    except caching exchange rates. Raises HeaderNotFoundError if the file has no usable header."""
    account = account.strip()
    if not account:
        raise ValueError("account name is required")
    reader = csv.reader(io.StringIO(text.lstrip("\ufeff")))
    rows = [(reader.line_num, row) for row in reader]
    header_index, cols = find_header([row for _, row in rows])

    prepared = PreparedFile(account=account, source_name=base_name(source_name))
    counter = OccurrenceCounter()
    for line, row in rows[header_index + 1 :]:
        if not any(cell.strip() for cell in row):
            continue  # blank or comma-only line
        prepared.rows_total += 1
        try:
            parsed = _parse_row(row, cols, rates)
        except _RowRejected as exc:
            prepared.errors.append((line, str(exc)))
            continue

        key = (
            account,
            parsed["txn_date"],
            parsed["orig_currency"],
            parsed["orig_amount_minor"],
            parsed["description_norm"],
        )
        parsed["fingerprint"] = fingerprint(*key, counter.next(*key))
        parsed["txn_date"] = parsed["txn_date"].isoformat()
        parsed["category_id"], parsed["category_source"] = categorize.categorize(
            parsed["description_norm"], rules, fallback_category
        )
        parsed["account"] = account
        parsed["line"] = line
        prepared.rows.append(parsed)
    return prepared


def insert_rows(conn: sqlite3.Connection, batch_id: int, rows: list[dict]) -> tuple[int, int]:
    """Insert prepared rows into a batch. Returns (imported, duplicate). Call inside `with conn:`."""
    imported = duplicate = 0
    for row in rows:
        if db.insert_transaction(conn, {**row, "batch_id": batch_id}):
            imported += 1
        else:
            duplicate += 1
    return imported, duplicate


def import_context(conn: sqlite3.Connection, fetch_rate: FetchRate) -> tuple[RateLookup, list, int]:
    """Rules, fallback category and rate lookup shared by every file of one import."""
    categorize.seed_defaults(conn)  # before any `with conn:` — it commits on its own
    return RateLookup(conn, fetch_rate), categorize.list_rules(conn), categorize.uncategorised_id(conn)


def import_csv(
    conn: sqlite3.Connection,
    text: str,
    account: str,
    source_name: str,
    fetch_rate: FetchRate = fx.fetch_rate,
) -> ImportReport:
    """Import one statement. Valid rows are stored, bad rows are reported, duplicates skipped.

    The whole file is one database transaction: either all valid rows land or none do.
    """
    rates, rules, fallback = import_context(conn, fetch_rate)
    prepared = prepare_file(conn, text, account, source_name, rates, rules, fallback)
    with conn:
        batch_id = db.create_batch(conn, source_name=prepared.source_name, account=prepared.account)
        imported, duplicate = insert_rows(conn, batch_id, prepared.rows)
        report = ImportReport(
            batch_id=batch_id,
            rows_total=prepared.rows_total,
            rows_imported=imported,
            rows_duplicate=duplicate,
            rows_rejected=len(prepared.errors),
            errors=prepared.errors,
        )
        db.finish_batch(conn, batch_id, report.rows_total, imported, duplicate, report.rows_rejected)

    # Counts and IDs only — never descriptions, merchants or amounts (privacy rule).
    log.info(
        "import batch=%d total=%d imported=%d duplicate=%d rejected=%d",
        batch_id,
        report.rows_total,
        report.rows_imported,
        report.rows_duplicate,
        report.rows_rejected,
    )
    return report


def import_file(
    conn: sqlite3.Connection, path: str, account: str, fetch_rate: FetchRate = fx.fetch_rate
) -> ImportReport:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return import_csv(conn, fh.read(), account=account, source_name=path, fetch_rate=fetch_rate)


def undo_import(conn: sqlite3.Connection, batch_id: int) -> int:
    """Delete exactly the transactions a batch inserted (and the batch). Returns rows removed."""
    removed = db.delete_batch(conn, batch_id)
    log.info("undo batch=%d removed=%d", batch_id, removed)
    return removed
