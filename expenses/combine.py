"""Combined import of several statements / accounts at once (spec rule I9).

preview() parses every file and finds transfer pairs between your own accounts without
writing anything. commit() imports everything in one transaction as one batch, marking the
transfer pairs the user confirmed.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import date

from expenses import categorize, db, fx
from expenses.importer import (
    FetchRate,
    HeaderNotFoundError,
    base_name,
    import_context,
    insert_rows,
    prepare_file,
)
from expenses.pdf_import import PdfImportError, is_pdf, pdf_to_csv

log = logging.getLogger(__name__)

TRANSFER_WINDOW_DAYS = 3


class CombinedImportError(ValueError):
    """The combined import can't go ahead (a file has a blocking problem)."""


@dataclass
class StatementInput:
    raw: str | bytes  # file content: CSV text, or bytes (UTF-8 CSV or a PDF, detected by content)
    account: str
    source_name: str
    password: str | None = None  # for password-protected PDFs; used once, never stored


@dataclass
class FilePreview:
    source_name: str
    account: str
    rows_total: int = 0
    new: int = 0
    duplicate: int = 0
    rejected: int = 0
    errors: list[tuple[int, str]] = field(default_factory=list)
    problem: str | None = None  # blocking: the whole combined import waits until it's fixed
    converted_csv: str | None = None  # for a PDF: the CSV it was converted to (I10), for download
    rows: list[dict] = field(default_factory=list, repr=False)


@dataclass
class PairSide:
    file_index: int
    account: str
    txn_date: date
    description: str
    amount_minor: int
    row: dict = field(repr=False, compare=False)


@dataclass
class TransferPair:
    debit: PairSide  # money leaving one of your accounts
    credit: PairSide  # the same money arriving in another

    @property
    def days_apart(self) -> int:
        return abs((self.credit.txn_date - self.debit.txn_date).days)


@dataclass
class CombinedPreview:
    files: list[FilePreview]
    pairs: list[TransferPair]

    @property
    def ok(self) -> bool:
        return bool(self.files) and all(f.problem is None for f in self.files)

    @property
    def new_rows(self) -> int:
        return sum(f.new for f in self.files)


@dataclass
class FileReport:
    source_name: str
    account: str
    imported: int
    duplicate: int
    rejected: int
    errors: list[tuple[int, str]]


@dataclass
class CombinedReport:
    batch_id: int
    files: list[FileReport]
    transfers_marked: int

    @property
    def rows_imported(self) -> int:
        return sum(f.imported for f in self.files)


# --- preview -------------------------------------------------------------------------------------------


def preview(
    conn: sqlite3.Connection, inputs: list[StatementInput], fetch_rate: FetchRate = fx.fetch_rate
) -> CombinedPreview:
    """Parse every file, count new/duplicate/rejected rows and find transfer pairs. Writes nothing."""
    rates, rules, fallback = import_context(conn, fetch_rate)
    pending: set[str] = set()  # fingerprints of new rows from earlier files in this set
    files: list[FilePreview] = []
    for item in inputs:
        f = FilePreview(source_name=base_name(item.source_name), account=item.account.strip())
        files.append(f)
        if not f.account:
            f.problem = "Pick or type the account this statement belongs to."
            continue
        if isinstance(item.raw, bytes) and is_pdf(item.raw):
            try:
                f.converted_csv = pdf_to_csv(item.raw, item.password).csv_text
            except PdfImportError as exc:
                f.problem = str(exc)[:1].upper() + str(exc)[1:] + "."
                continue
        try:
            if f.converted_csv is not None:
                text = f.converted_csv
            else:
                text = item.raw.decode("utf-8-sig") if isinstance(item.raw, bytes) else item.raw
            prepared = prepare_file(conn, text, f.account, item.source_name, rates, rules, fallback)
        except UnicodeDecodeError:
            f.problem = "Not UTF-8 text. Export the statement as CSV (UTF-8)."
            continue
        except HeaderNotFoundError:
            f.problem = "No header row with date, description and amount columns."
            continue

        f.rows_total, f.errors, f.rejected = prepared.rows_total, prepared.errors, len(prepared.errors)
        stored = db.existing_fingerprints(conn, [r["fingerprint"] for r in prepared.rows])
        for row in prepared.rows:
            row["is_new"] = row["fingerprint"] not in stored and row["fingerprint"] not in pending
            if row["is_new"]:
                f.new += 1
                pending.add(row["fingerprint"])
            else:
                f.duplicate += 1
        f.rows = prepared.rows
    return CombinedPreview(files=files, pairs=find_transfer_pairs(files))


def _side(file_index: int, row: dict) -> PairSide:
    return PairSide(
        file_index,
        row["account"],
        date.fromisoformat(row["txn_date"]),
        row["description_norm"],
        row["amount_minor"],
        row,
    )


def find_transfer_pairs(files: list[FilePreview]) -> list[TransferPair]:
    """Same INR amount, opposite sign, different accounts, <= 3 days apart; each row used once.

    Debits are matched in date order, each to the unused credit with the closest date
    (ties: earliest credit, then file order).
    """
    usable = [(i, row) for i, f in enumerate(files) if f.problem is None for row in f.rows if row["is_new"]]
    debits = sorted(
        (_side(i, r) for i, r in usable if r["amount_minor"] < 0), key=lambda s: (s.txn_date, s.file_index)
    )
    credits = [_side(i, r) for i, r in usable if r["amount_minor"] > 0]
    used: set[int] = set()
    pairs: list[TransferPair] = []
    for debit in debits:
        best = None
        for index, credit in enumerate(credits):
            if index in used or credit.amount_minor != -debit.amount_minor or credit.account == debit.account:
                continue
            gap = abs((credit.txn_date - debit.txn_date).days)
            if gap > TRANSFER_WINDOW_DAYS:
                continue
            rank = (gap, credit.txn_date, credit.file_index, index)
            if best is None or rank < best[0]:
                best = (rank, index)
        if best is not None:
            used.add(best[1])
            pairs.append(TransferPair(debit, credits[best[1]]))
    return pairs


# --- commit ---------------------------------------------------------------------------------------------


def _transfer_category(conn: sqlite3.Connection) -> int:
    for category in categorize.list_categories(conn):
        if category["kind"] == "transfer":
            return category["id"]
    return categorize.add_category(conn, "Transfers", "transfer", "#94A3B8")


def commit(
    conn: sqlite3.Connection, plan: CombinedPreview, confirmed: set[int] | None = None
) -> CombinedReport:
    """Import every file of a preview in ONE transaction and ONE batch (all or nothing).

    `confirmed`: indexes into plan.pairs to save as Transfers (default: all of them).
    """
    if not plan.ok:
        raise CombinedImportError(
            "Fix or remove the files marked with a problem first; nothing was imported."
        )
    confirmed = set(range(len(plan.pairs))) if confirmed is None else set(confirmed)
    marked = {id(side.row) for i in confirmed for side in (plan.pairs[i].debit, plan.pairs[i].credit)}
    transfer_id = _transfer_category(conn) if marked else None  # may commit on its own: before `with conn`

    accounts = list(dict.fromkeys(f.account for f in plan.files))
    reports: list[FileReport] = []
    with conn:
        batch_id = db.create_batch(
            conn, source_name=", ".join(f.source_name for f in plan.files), account=" + ".join(accounts)
        )
        for f in plan.files:
            rows = []
            for original in f.rows:
                row = dict(original)
                if id(original) in marked:  # user-confirmed transfer: rules must never overwrite it
                    row["category_id"], row["category_source"] = transfer_id, "manual"
                rows.append(row)
            imported, duplicate = insert_rows(conn, batch_id, rows)
            reports.append(FileReport(f.source_name, f.account, imported, duplicate, f.rejected, f.errors))
        db.finish_batch(
            conn,
            batch_id,
            sum(f.rows_total for f in plan.files),
            sum(r.imported for r in reports),
            sum(r.duplicate for r in reports),
            sum(r.rejected for r in reports),
        )

    # Counts and IDs only (privacy rule).
    log.info(
        "combined import batch=%d files=%d imported=%d transfers=%d",
        batch_id,
        len(reports),
        sum(r.imported for r in reports),
        len(confirmed),
    )
    return CombinedReport(batch_id=batch_id, files=reports, transfers_marked=len(confirmed))
