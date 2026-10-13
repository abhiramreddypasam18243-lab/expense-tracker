"""Step 18 — robust column detection for real bank layouts (AC-94 to AC-98)."""

import re

import pytest

from expenses import categorize as cat
from expenses import db
from expenses.importer import HeaderNotFoundError, find_header, import_csv
from expenses.pdf_import import PdfImportError, pdf_to_csv
from tools.make_synthetic import table_pdf

AXIS = ["Tran Date", "Chq No", "Particulars", "Debit", "Credit", "Balance"]
ICICI = [
    "S No.",
    "Value Date",
    "Transaction Date",
    "Cheque Number",
    "Transaction Remarks",
    "Withdrawal Amount (INR )",
    "Deposit Amount (INR )",
    "Balance (INR )",
]
KOTAK = ["Date", "Narration", "Chq/Ref No", "Withdrawal (Dr)", "Deposit (Cr)", "Balance"]
CARD = ["Date", "Transaction Details", "Amount (in Rs.)"]


def no_network(currency, on):
    raise AssertionError("tests must never touch the network")


def imported(csv_text: str) -> list[tuple]:
    conn = db.connect(":memory:")
    cat.seed_defaults(conn)
    report = import_csv(conn, csv_text, "Bank", "s.csv", fetch_rate=no_network)
    rows = conn.execute(
        "SELECT txn_date, description_norm, amount_minor FROM transactions ORDER BY id"
    ).fetchall()
    conn.close()
    assert report.rows_rejected == 0, report.errors
    return [tuple(r) for r in rows]


# --- AC-94 real-world headings ------------------------------------------------------------------


def test_ac_94_axis_icici_kotak_card_headings():
    _, axis = find_header([AXIS])
    assert (axis.date, axis.description, axis.debit, axis.credit, axis.amount) == (0, 2, 3, 4, None)

    _, icici = find_header([ICICI])
    assert icici.date == 2  # transaction date, not value date (column 1)
    assert (icici.description, icici.debit, icici.credit) == (4, 5, 6)

    _, kotak = find_header([KOTAK])
    assert (kotak.date, kotak.description, kotak.debit, kotak.credit) == (0, 1, 3, 4)

    _, card = find_header([CARD])
    assert (card.date, card.description, card.amount) == (0, 1, 2)

    for cols, header in ((axis, AXIS), (icici, ICICI), (kotak, KOTAK)):
        picked = {cols.date, cols.description, cols.debit, cols.credit}
        assert not any(re.search("balance|chq|cheque|ref", header[i], re.I) for i in picked)


def test_ac_94_old_aliases_still_work():
    _, cols = find_header([["Txn Date", "Narration", "Withdrawal Amt.", "Deposit_Amt", "Closing Balance"]])
    assert (cols.date, cols.description, cols.debit, cols.credit) == (0, 1, 2, 3)


# --- AC-95 two-row headings ------------------------------------------------------------------------


def test_ac_95_two_row_heading():
    rows = [
        ["Txn", "Narration", "Withdrawal", "Deposit"],
        ["Date", "", "Amt.", "Amt."],
        ["05/03/2026", "UPI/R1/SWIGGY/OKAXIS/x", "500.00", ""],
    ]
    index, cols = find_header(rows)
    assert index == 1  # data starts after the second heading row
    assert (cols.date, cols.description, cols.debit, cols.credit) == (0, 1, 2, 3)
    text = "Txn,Narration,Withdrawal,Deposit\nDate,,Amt.,Amt.\n05/03/2026,UPI/R1/SWIGGY/OKAXIS/x,500.00,\n"
    assert imported(text) == [("2026-03-05", "UPI/R1/SWIGGY/OKAXIS/X", -50000)]


# --- AC-96 separate Dr/Cr column --------------------------------------------------------------------


def test_ac_96_indicator_column():
    text = (
        "Date,Description,Amount,Dr/Cr\n"
        "05/03/2026,POS 1 BIG BAZAAR,500.00,Dr\n"
        "06/03/2026,REFUND BIG BAZAAR,500.00,CR\n"
        "07/03/2026,NEFT SALARY,85000.00,C\n"
    )
    assert [r[2] for r in imported(text)] == [-50000, 50000, 8_500_000]
    conn = db.connect(":memory:")
    cat.seed_defaults(conn)
    report = import_csv(
        conn, "Date,Description,Amount,Type\n05/03/2026,X,5.00,MAYBE\n", "B", "s.csv", fetch_rate=no_network
    )
    assert report.errors == [(2, "invalid amount")]
    conn.close()


# --- AC-97 PDFs in each style -------------------------------------------------------------------------


def axis_rows():
    rows = []
    for d in range(1, 29):
        rows.append(
            [
                f"{d:02d}/03/2026",
                "",
                f"UPI/R{d}/SWIGGY/OKAXIS/order {d}",
                f"{d * 10}.00",
                "",
                f"{50000 - d * 10:,}.00",
            ]
        )
        if d % 3 == 0:
            rows.append(
                [
                    f"{d:02d}/03/2026",
                    f"{d:06d}",
                    f"NEFT SALARY PART {d}",
                    "",
                    f"{d * 100:,}.00",
                    f"{60000 + d:,}.00",
                ]
            )
    return rows


@pytest.mark.parametrize("ruled", [True, False])
def test_ac_97_axis_pdf_ruled_and_unruled(ruled):
    conversion = pdf_to_csv(table_pdf(AXIS, axis_rows(), ["AXIS BANK - Statement of Account"], ruled=ruled))
    rows = imported(conversion.csv_text)
    assert len(rows) == 28 + 9
    assert rows[0] == ("2026-03-01", "UPI/R1/SWIGGY/OKAXIS/ORDER 1", -1000)
    assert ("2026-03-03", "NEFT SALARY PART 3", 30000) in rows


def test_ac_97_icici_kotak_card_and_two_row_pdfs():
    icici = [
        [
            str(i),
            f"{i:02d}/03/2026",
            f"{i + 1:02d}/03/2026",
            "",
            f"UPI/R{i}/ZOMATO/YBL/x",
            f"{i}.00",
            "",
            "9,999.00",
        ]
        for i in range(1, 6)
    ]
    rows = imported(pdf_to_csv(table_pdf(ICICI, icici, ["ICICI BANK"])).csv_text)
    assert rows[0] == ("2026-03-02", "UPI/R1/ZOMATO/YBL/X", -100)  # transaction date, not value date

    kotak = [
        [f"{i:02d}/03/2026", f"IMPS RENT REF{i}", f"REF{i}", "", f"{i},000.00", "1.00"] for i in range(1, 4)
    ]
    assert [r[2] for r in imported(pdf_to_csv(table_pdf(KOTAK, kotak, ["KOTAK"])).csv_text)] == [
        100000,
        200000,
        300000,
    ]

    # Card statements print "Dr"/"Cr" after the amount: a spend is Dr, a payment is Cr.
    card = [["05/03/2026", "AMAZON RETAIL", "1,999.00 Dr"], ["09/03/2026", "PAYMENT RECEIVED", "5,000.00 Cr"]]
    assert [r[2] for r in imported(pdf_to_csv(table_pdf(CARD, card, ["CARD"])).csv_text)] == [-199900, 500000]

    two = table_pdf(
        [["Txn", "Narration", "Withdrawal", "Deposit"], ["Date", "", "Amt.", "Amt."]],
        [["05/03/2026", "POS 1 BIG BAZAAR", "750.00", ""]],
        ["BANK"],
    )
    assert imported(pdf_to_csv(two).csv_text) == [("2026-03-05", "POS 1 BIG BAZAAR", -75000)]


# --- AC-98 errors list headings only -----------------------------------------------------------------------


def test_ac_98_error_lists_headings_without_values():
    rows = [
        ["Posting Ref", "Memo", "Value"],
        ["REF123", "SECRET SHOP", "999.00"],
        ["REF124", "OTHER", "5.00"],
    ]
    with pytest.raises(HeaderNotFoundError) as exc:
        find_header(rows)
    message = str(exc.value)
    assert "Columns we saw: Posting Ref | Memo | Value" in message
    assert not re.search(r"\d", message.split("Columns we saw:")[1])
    assert "SECRET" not in message

    pdf = table_pdf(["Posting Ref", "Memo", "Value"], [["REF123", "SECRET SHOP", "999.00"]] * 3, ["BANK"])
    with pytest.raises(PdfImportError) as pdf_exc:
        pdf_to_csv(pdf)
    assert "Columns we saw: Posting Ref | Memo | Value" in str(pdf_exc.value)
    assert "SECRET" not in str(pdf_exc.value) and "999" not in str(pdf_exc.value)
