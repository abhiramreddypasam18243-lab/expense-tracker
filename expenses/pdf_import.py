"""PDF bank statements -> CSV text (spec rule I10).

pdfplumber reads each page's table cell by cell. The rows are cleaned (line breaks inside cells,
wrapped narrations, headers repeated on every page, blank rows) and written as CSV, which then
goes through the normal import (I1-I9). Everything happens in memory; the password is used once
and never stored, logged or included in messages.
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass

import pdfplumber
from pdfminer.pdfdocument import PDFPasswordIncorrect

from expenses.importer import HeaderNotFoundError, find_header

log = logging.getLogger(__name__)

MIN_TEXT_CHARS = 20  # less selectable text than this on every page = a scan
TEXT_TABLE_SETTINGS = {"vertical_strategy": "text", "horizontal_strategy": "text"}


class PdfImportError(ValueError):
    """A PDF statement can't be converted. The message is meant for the user."""


@dataclass
class PdfConversion:
    csv_text: str
    pages: int
    rows: int


def is_pdf(data: bytes) -> bool:
    """By content, not by file name: PDFs start with '%PDF' (a few bytes of junk are tolerated)."""
    return b"%PDF" in data[:1024]


def _is_password_error(exc: BaseException) -> bool:
    seen: BaseException | None = exc
    while seen is not None:
        found = [seen, *seen.args]
        if any(isinstance(item, PDFPasswordIncorrect) for item in found):
            return True
        seen = seen.__cause__ or seen.__context__
    return False


def _clean(cell: object) -> str:
    """Trim and turn line breaks inside a cell into single spaces."""
    return " ".join(str(cell or "").split())


def _read_rows(data: bytes, password: str | None) -> tuple[int, int, list[list[str]]]:
    """(pages, characters of text, table rows) — raises PdfImportError for unreadable files."""
    try:
        with pdfplumber.open(io.BytesIO(data), password=password or "") as pdf:
            text_chars, rows = 0, []
            for page in pdf.pages:
                text_chars += len((page.extract_text() or "").strip())
                tables = page.extract_tables() or page.extract_tables(TEXT_TABLE_SETTINGS)
                for table in tables:
                    rows += [[_clean(cell) for cell in row] for row in table]
            return len(pdf.pages), text_chars, rows
    except Exception as exc:  # noqa: BLE001 — pdfplumber/pdfminer raise many types; map them to messages
        if _is_password_error(exc):
            if password:
                raise PdfImportError(
                    "wrong password for this PDF — check the bank's email for the format"
                ) from None
            raise PdfImportError(
                "this PDF is password-protected — enter its password "
                "(often your date of birth or customer ID)"
            ) from None
        raise PdfImportError("this file couldn't be read as a PDF") from None


def pdf_to_csv(data: bytes, password: str | None = None) -> PdfConversion:
    if not is_pdf(data):
        raise PdfImportError("this is not a PDF file")
    pages, text_chars, rows = _read_rows(data, password)
    if text_chars < MIN_TEXT_CHARS:
        raise PdfImportError(
            "this looks like a scanned PDF, so its text can't be read — download the statement again from "
            "net banking, or export it as CSV"
        )
    rows = [row for row in rows if any(row)]
    try:
        header_index, cols = find_header(rows)
    except HeaderNotFoundError:
        raise PdfImportError(
            "no transaction table found in this PDF (looked for date, description and amount columns)"
        ) from None

    header = rows[header_index]
    width = len(header)
    amount_cols = [i for i in (cols.amount, cols.debit, cols.credit) if i is not None]
    out = [header]
    for row in rows[header_index + 1 :]:
        row = (row + [""] * width)[:width]
        if [c.lower() for c in row] == [c.lower() for c in header]:
            continue  # the header printed again at the top of a later page
        if not row[cols.date] and len(out) > 1 and not any(row[i] for i in amount_cols):
            extra = " ".join(c for c in row if c)  # a narration wrapped onto its own row
            out[-1][cols.description] = f"{out[-1][cols.description]} {extra}".strip()
            continue
        out.append(row)

    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(out)
    log.info("pdf converted pages=%d rows=%d", pages, len(out) - 1)  # counts only (privacy rule)
    return PdfConversion(buf.getvalue(), pages, len(out) - 1)
