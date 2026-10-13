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
from itertools import zip_longest

from expenses import categorize, db, fx
from expenses.dates import DateParseError, parse_date
from expenses.fingerprint import OccurrenceCounter, fingerprint
from expenses.money import MoneyParseError, parse_amount, parse_debit_credit

log = logging.getLogger(__name__)

HEADER_SEARCH_ROWS = 20

# Known column names, already reduced by _key() (lower-case letters and digits only).
_KNOWN = {
    "date": {
        "date",
        "txndate",
        "transactiondate",
        "trandate",
        "valuedate",
        "valuedt",
        "postingdate",
        "postdate",
        "txnpostingdate",
        "transactionpostingdate",
        "bookingdate",
        "entrydate",
        "trndate",
    },
    "description": {
        "description",
        "narration",
        "particulars",
        "details",
        "remarks",
        "transactiondetails",
        "transactionremarks",
        "transactiondescription",
        "narrative",
        "txndetails",
        "txnremarks",
    },
    "amount": {"amount", "txnamount", "transactionamount", "amt"},
    "debit": {
        "debit",
        "debits",
        "withdrawal",
        "withdrawals",
        "withdrawalamt",
        "withdrawalamount",
        "debitamount",
        "debitamt",
        "dr",
        "withdrawaldr",
        "dramount",
    },
    "credit": {
        "credit",
        "credits",
        "deposit",
        "deposits",
        "depositamt",
        "depositamount",
        "creditamount",
        "creditamt",
        "cr",
        "depositcr",
        "cramount",
    },
    "indicator": {
        "drcr",
        "crdr",
        "debitcredit",
        "creditdebit",
        "dc",
        "cd",
        "type",
        "txntype",
        "transactiontype",
    },
    "currency": {"currency", "ccy", "curr", "currencycode"},
}
_KNOWN_BY_KEY = {key: field for field, keys in _KNOWN.items() for key in keys}
_IGNORE_WORDS = ("balance", "cheque", "chq", "ref", "srno", "sno", "slno", "serial", "instrument")
_DESCRIPTION_WORDS = ("narration", "particular", "description", "remark", "detail", "narrative")
_DEBIT_FLAGS = {"dr", "d", "db", "debit", "withdrawal"}
_CREDIT_FLAGS = {"cr", "c", "credit", "deposit"}

FetchRate = Callable[[str, date], "tuple[Decimal, date] | None"]


class HeaderNotFoundError(ValueError):
    """No usable header row. `seen` lists the headings that were found (never cells with digits)."""

    def __init__(self, message: str, seen: list[str] | None = None) -> None:
        self.seen = seen or []
        if self.seen:
            message += ". Columns we saw: " + " | ".join(self.seen)
        super().__init__(message)


@dataclass
class ColumnMap:
    date: int
    description: int
    amount: int | None = None
    debit: int | None = None
    credit: int | None = None
    currency: int | None = None
    indicator: int | None = None  # a separate "Dr/Cr" column next to a single amount column
    header: list[str] = field(default_factory=list)  # the heading cells (two rows joined if split)


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


def _key(cell: str) -> str:
    """'Withdrawal Amount (INR )' -> 'withdrawalamount'; 'Amount (in Rs.)' -> 'amount'.

    Currency words are removed only as separate words, so 'Particulars' keeps its 'rs'.
    """
    text = re.sub(r"\b(?:in\s+)?(?:inr|rs)\b", " ", cell.lower())
    return re.sub(r"[^a-z0-9]", "", text)


def _classify(key: str) -> str | None:
    if not key:
        return None
    if key in _KNOWN_BY_KEY:
        return _KNOWN_BY_KEY[key]
    if any(word in key for word in _IGNORE_WORDS):
        return None
    if key.endswith(("drcr", "crdr")):
        return "indicator"
    if "withdraw" in key or key.startswith("debit"):
        return "debit"
    if "deposit" in key or key.startswith("credit"):
        return "credit"
    if any(word in key for word in _DESCRIPTION_WORDS):
        return "description"
    if "date" in key or key.endswith("dt"):
        return "date"
    if "amount" in key:
        return "amount"
    return None


def _date_rank(key: str) -> int:
    """Lower is better: the transaction/posting date beats the value date."""
    if any(word in key for word in ("txn", "tran", "post", "booking", "entry")):
        return 0
    if key == "date":
        return 1
    if "value" in key:
        return 3
    return 2


def _match(cells: list[str]) -> ColumnMap | None:
    found: dict[str, int] = {}
    dates: list[tuple[int, int]] = []
    for col, cell in enumerate(cells):
        key = _key(cell)
        kind = _classify(key)
        if kind == "date":
            dates.append((_date_rank(key), col))
        elif kind and kind not in found:
            found[kind] = col
    has_amount = "amount" in found
    has_pair = "debit" in found and "credit" in found
    if not dates or "description" not in found or not (has_amount or has_pair):
        return None
    cols = ColumnMap(
        date=min(dates)[1],
        description=found["description"],
        currency=found.get("currency"),
        header=list(cells),
    )
    if has_amount:  # amount wins over a debit/credit pair
        cols.amount, cols.indicator = found["amount"], found.get("indicator")
    else:
        cols.debit, cols.credit = found["debit"], found["credit"]
    return cols


def _headings_seen(rows: list[list[str]]) -> list[str]:
    """The most heading-like row's short, digit-free cells (safe to show: no amounts or dates)."""
    best: list[str] = []
    for row in rows:
        cells = [c.strip() for c in row if c.strip() and len(c.strip()) <= 30 and not re.search(r"\d", c)]
        if len(cells) >= 2 and len(cells) > len(best):
            best = cells
    return best


def find_header(rows: list[list[str]], limit: int = HEADER_SEARCH_ROWS) -> tuple[int, ColumnMap]:
    """Return (index of the last heading row, column map). Data starts on the next row.

    Single rows are tried first; then each pair of rows joined cell by cell (a heading split
    over two lines, e.g. 'Withdrawal' above 'Amt.').
    """
    window = rows[:limit]
    for index, row in enumerate(window):
        cols = _match(row)
        if cols:
            return index, cols
    for index in range(len(window) - 1):
        merged = [
            " ".join(part for part in pair if part).strip()
            for pair in zip_longest(window[index], window[index + 1], fillvalue="")
        ]
        cols = _match(merged)
        if cols:
            return index + 1, cols
    raise HeaderNotFoundError(
        f"no header with date, description and amount columns in the first {limit} lines",
        _headings_seen(window),
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


def _signed_by_indicator(amount_text: str, flag_text: str) -> int:
    """Amount plus a separate Dr/Cr column: 'Dr' makes it a debit, 'Cr' a credit."""
    flag = flag_text.strip().strip(".").lower()
    value = parse_amount(amount_text)
    if not flag:
        return value
    if flag in _DEBIT_FLAGS:
        return -abs(value)
    if flag in _CREDIT_FLAGS:
        return abs(value)
    raise MoneyParseError("unknown debit/credit indicator")


def _parse_row(row: list[str], cols: ColumnMap, rates: RateLookup) -> dict:
    try:
        txn_date = parse_date(_cell(row, cols.date))
    except DateParseError:
        raise _RowRejected("invalid date") from None

    description = _cell(row, cols.description).strip()
    if not description:
        raise _RowRejected("empty description")

    try:
        if cols.amount is not None and cols.indicator is not None:
            orig_amount = _signed_by_indicator(_cell(row, cols.amount), _cell(row, cols.indicator))
        elif cols.amount is not None:
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
    inferred: object | None = None  # columns.Inference when the file had no heading row (I11)


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
    column_roles: dict[str, int] | None = None,
) -> PreparedFile:
    """Parse, normalise, fingerprint and categorise one statement (I1-I5, I8). No database writes,
    except caching exchange rates. Without a heading row (or with `column_roles` chosen by the user)
    the columns are inferred from the data (I11). Raises HeaderNotFoundError if that fails too."""
    account = account.strip()
    if not account:
        raise ValueError("account name is required")
    reader = csv.reader(io.StringIO(text.lstrip("\ufeff")))
    rows = [(reader.line_num, row) for row in reader]
    from expenses.columns import infer_columns  # here, not at the top: columns imports this module

    table = [row for _, row in rows]
    inferred = None
    if column_roles is not None:
        inferred = infer_columns(table, column_roles)
    else:
        try:
            header_index, cols = find_header(table)
        except HeaderNotFoundError as exc:
            try:
                inferred = infer_columns(table)
            except HeaderNotFoundError as inner:
                raise HeaderNotFoundError(str(inner), exc.seen) from None
    if inferred is not None:
        header_index, cols = inferred.start - 1, inferred.column_map()

    prepared = PreparedFile(account=account, source_name=base_name(source_name), inferred=inferred)
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
