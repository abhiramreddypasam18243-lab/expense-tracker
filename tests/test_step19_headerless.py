"""Step 19 — statements without a heading row (AC-99 to AC-104)."""

import csv
import io

import pytest

from expenses import categorize as cat
from expenses import db
from expenses.columns import infer_columns
from expenses.combine import StatementInput, preview
from expenses.importer import HeaderNotFoundError, import_csv
from expenses.pdf_import import PdfImportError, pdf_to_csv
from tools.make_synthetic import table_pdf

# (date, narration, cheque no, signed paise)
TXNS = [
    ("01/03/2026", "UPI/R1/SWIGGY/OKAXIS/lunch", "", -50000),
    ("02/03/2026", "NEFT SALARY ACME", "", 8_500_000),
    ("03/03/2026", "CHQ PAID TO LANDLORD", "000123", -2_200_000),
    ("05/03/2026", "POS 1 BIG BAZAAR", "", -125050),
    ("06/03/2026", "REFUND AMAZON", "", 49900),
    ("08/03/2026", "UPI/R2/ZOMATO/YBL/dinner", "", -64000),
    ("09/03/2026", "ATM WDL 0042", "", -500000),
    ("11/03/2026", "IMPS FROM SBI", "", 1_000_000),
]
PREAMBLE = [["MY BANK LTD"], ["Account No: XXXX1234"], ["Statement for March 2026"], []]


def money(paise: int) -> str:
    return f"{paise // 100:,}.{paise % 100:02d}"


def table(credit_first=False, balance=True, cheque=True):
    rows, bal = [], 50_000_00
    for d, desc, chq, paise in TXNS:
        bal += paise
        debit, credit = (money(-paise), "") if paise < 0 else ("", money(paise))
        row = [d, desc] + ([chq] if cheque else []) + ([credit, debit] if credit_first else [debit, credit])
        rows.append(row + ([money(bal)] if balance else []))
    return rows


def to_csv(rows, preamble=True) -> str:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows((PREAMBLE if preamble else []) + rows)
    return buf.getvalue()


def no_network(currency, on):
    raise AssertionError("tests must never touch the network")


def amounts(text: str) -> list[int]:
    conn = db.connect(":memory:")
    cat.seed_defaults(conn)
    report = import_csv(conn, text, "Bank", "s.csv", fetch_rate=no_network)
    assert report.rows_rejected == 0, report.errors
    values = [r[0] for r in conn.execute("SELECT amount_minor FROM transactions ORDER BY id")]
    conn.close()
    return values


EXPECTED = [paise for *_, paise in TXNS]


# --- AC-99 debit/credit/balance ------------------------------------------------------------------


def test_ac_99_debit_credit_balance_with_preamble():
    rows = PREAMBLE + table()
    inference = infer_columns(rows)
    assert inference.roles == {"date": 0, "description": 1, "debit": 3, "credit": 4, "balance": 5}
    assert inference.balance_checked and inference.start == len(PREAMBLE)
    assert amounts(to_csv(table())) == EXPECTED


def test_ac_99_credit_column_first_is_fixed_by_the_balance():
    inference = infer_columns(table(credit_first=True))
    assert (inference.roles["credit"], inference.roles["debit"]) == (3, 4) and inference.balance_checked
    assert amounts(to_csv(table(credit_first=True))) == EXPECTED


# --- AC-100 one money column ----------------------------------------------------------------------


def test_ac_100_single_amount_layouts():
    signed = [[d, desc, f"{'-' if p < 0 else ''}{money(abs(p))}"] for d, desc, _, p in TXNS]
    assert amounts(to_csv(signed)) == EXPECTED

    suffix = [[d, desc, f"{money(abs(p))} {'Dr' if p < 0 else 'Cr'}"] for d, desc, _, p in TXNS]
    assert amounts(to_csv(suffix)) == EXPECTED

    indicator = [[d, desc, money(abs(p)), "DR" if p < 0 else "CR"] for d, desc, _, p in TXNS]
    assert infer_columns(indicator).roles == {"date": 0, "description": 1, "amount": 2, "indicator": 3}
    assert amounts(to_csv(indicator)) == EXPECTED

    two_dates = [
        [d, d.replace("/03/", "/04/"), desc, f"{'-' if p < 0 else ''}{money(abs(p))}"]
        for d, desc, _, p in TXNS
    ]
    assert infer_columns(two_dates).roles["date"] == 0


# --- AC-101 no balance ----------------------------------------------------------------------------


def test_ac_101_without_balance_left_is_debit_and_flagged():
    inference = infer_columns(table(balance=False))
    assert (inference.roles["debit"], inference.roles["credit"]) == (3, 4)
    assert inference.balance_checked is False


# --- AC-102 user correction -----------------------------------------------------------------------


def test_ac_102_column_roles_override():
    conn = db.connect(":memory:")
    cat.seed_defaults(conn)
    text = to_csv(table())
    guessed = preview(conn, [StatementInput(text, "Bank", "s.csv")], fetch_rate=no_network)
    file = guessed.files[0]
    assert file.inferred is not None and file.inferred.balance_checked and file.new == len(TXNS)
    assert len(file.inferred.sample) == 3

    swapped = {"date": 0, "description": 1, "debit": 4, "credit": 3, "balance": 5}
    flipped = preview(
        conn, [StatementInput(text, "Bank", "s.csv", column_roles=swapped)], fetch_rate=no_network
    )
    signs = [row["amount_minor"] for row in flipped.files[0].rows]
    assert signs == [-p for p in EXPECTED]

    bad = preview(
        conn,
        [StatementInput(text, "Bank", "s.csv", column_roles={"date": 0, "debit": 3})],
        fetch_rate=no_network,
    )
    assert bad.files[0].problem and "description" in bad.files[0].problem.lower() and not bad.ok
    conn.close()


# --- AC-103 PDFs and failures ---------------------------------------------------------------------


def test_ac_103_headerless_pdf_and_unrecognisable_files():
    rows = table()
    pdf = table_pdf(rows[0], rows[1:], ["MY BANK LTD", "Statement for March 2026"])  # no heading row at all
    conversion = pdf_to_csv(pdf)
    assert conversion.csv_text.splitlines()[0].startswith("Date,Description")
    assert conversion.inferred is not None and conversion.inferred.balance_checked
    assert amounts(conversion.csv_text) == EXPECTED

    with pytest.raises(HeaderNotFoundError, match="no heading row"):
        infer_columns([["foo", "bar", "baz"], ["one", "two", "three"], ["four", "five", "six"]])
    with pytest.raises(PdfImportError, match="no heading row"):
        pdf_to_csv(table_pdf(["foo", "bar", "baz"], [["one", "two", "three"]] * 4, ["BANK"]))
