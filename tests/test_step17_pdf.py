"""Step 17 — PDF statements (AC-88 to AC-93)."""

import csv
import io
import logging
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from expenses import categorize as cat
from expenses import cli, db
from expenses.combine import StatementInput, preview
from expenses.importer import import_csv
from expenses.pdf_import import PdfImportError, is_pdf, pdf_to_csv
from tools.make_synthetic import generate_csv, generate_pdf, table_pdf

ROOT = Path(__file__).resolve().parent.parent
HEADER = ["Txn Date", "Narration", "Withdrawal Amt", "Deposit Amt"]


def no_network(currency, on):
    raise AssertionError("tests must never touch the network")


def stored(text: str) -> list[tuple]:
    conn = db.connect(":memory:")
    cat.seed_defaults(conn)
    import_csv(conn, text, "HDFC", "s.csv", fetch_rate=no_network)
    rows = conn.execute(
        "SELECT txn_date, description_norm, amount_minor FROM transactions"
        " ORDER BY txn_date, description_norm, amount_minor"
    ).fetchall()
    conn.close()
    return [tuple(r) for r in rows]


# --- AC-88 round trip -----------------------------------------------------------------------------


def test_ac_88_pdf_imports_like_the_csv():
    pdf = generate_pdf(42, months=2)
    assert is_pdf(pdf)
    conversion = pdf_to_csv(pdf)
    assert conversion.pages >= 2
    from_pdf, from_csv = stored(conversion.csv_text), stored(generate_csv(42, "debit_credit", months=2))
    assert len(from_pdf) == len(from_csv) >= 90  # two months of synthetic data
    assert from_pdf == from_csv


# --- AC-89 continuation rows, repeated headers, line breaks ---------------------------------------


def test_ac_89_cleanup_rules():
    rows = [
        ["05/03/2026", "UPI/R1/SWIGGY/OKAXIS/lunch", "500.00", ""],
        ["", "WITH FRIENDS AT OFFICE", "", ""],  # continuation of the narration above
        ["", "", "", ""],  # blank
        ["06/03/2026", "NEFT SALARY ACME", "", "85,000.00"],
    ] + [[f"{day:02d}/03/2026", f"POS {day} BIG BAZAAR", "100.00", ""] for day in range(7, 28)] * 4
    conversion = pdf_to_csv(table_pdf(HEADER, rows, ["MY BANK - Statement"]))
    parsed = list(csv.reader(io.StringIO(conversion.csv_text)))
    assert parsed[0] == HEADER
    assert sum(1 for r in parsed if r == HEADER) == 1  # header repeated on later pages is dropped
    assert parsed[1] == ["05/03/2026", "UPI/R1/SWIGGY/OKAXIS/lunch WITH FRIENDS AT OFFICE", "500.00", ""]
    assert parsed[2][1] == "NEFT SALARY ACME" and all(any(cell for cell in r) for r in parsed)
    assert all("\n" not in cell for r in parsed for cell in r)
    assert conversion.rows == len(parsed) - 1 == 2 + 21 * 4


# --- AC-90 passwords ------------------------------------------------------------------------------


def test_ac_90_password_protected(caplog):
    caplog.set_level(logging.DEBUG)
    locked = generate_pdf(7, months=1, password="Dob01011990")
    with pytest.raises(PdfImportError, match="password-protected") as missing:
        pdf_to_csv(locked)
    with pytest.raises(PdfImportError, match="wrong password") as wrong:
        pdf_to_csv(locked, password="Dob31121999")
    converted = pdf_to_csv(locked, password="Dob01011990")
    assert stored(converted.csv_text)
    for text in (str(missing.value), str(wrong.value), caplog.text):
        assert "Dob01011990" not in text and "Dob31121999" not in text


# --- AC-91 other problems -------------------------------------------------------------------------


def scanned_pdf() -> bytes:
    from fpdf import FPDF
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (800, 300), "white")
    ImageDraw.Draw(image).text((20, 20), "05/03/2026  SWIGGY  500.00", fill="black")
    pdf = FPDF()
    pdf.add_page()
    pdf.image(image, x=10, y=10, w=180)
    return bytes(pdf.output())


def text_only_pdf() -> bytes:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    pdf.multi_cell(0, 6, "Dear customer, thank you for banking with us. This letter has no statement table.")
    return bytes(pdf.output())


def test_ac_91_clear_errors():
    with pytest.raises(PdfImportError, match="scanned"):
        pdf_to_csv(scanned_pdf())
    with pytest.raises(PdfImportError, match="not a PDF"):
        pdf_to_csv(b"Date,Description,Amount\n2026-01-01,A,-1\n")
    with pytest.raises(PdfImportError, match="no transaction table"):
        pdf_to_csv(text_only_pdf())


# --- AC-92 combined import and CLI ----------------------------------------------------------------


def test_ac_92_preview_mixes_pdf_and_csv():
    conn = db.connect(":memory:")
    cat.seed_defaults(conn)
    csv_text = "Date,Description,Amount\n2026-03-05,UPI/R1/SWIGGY/OKAXIS/x,-500.00\n"
    locked = generate_pdf(7, months=1, password="pw-123")
    plan = preview(
        conn,
        [
            StatementInput(generate_pdf(42, months=1), "HDFC", "hdfc.pdf"),
            StatementInput(csv_text.encode(), "ICICI", "icici.csv"),
            StatementInput(
                locked, "SBI", "renamed.csv"
            ),  # a PDF with the wrong extension: detected by content
        ],
        fetch_rate=no_network,
    )
    hdfc, icici, sbi = plan.files
    assert hdfc.problem is None and hdfc.new > 40 and hdfc.converted_csv.startswith("Txn Date,Narration")
    assert icici.problem is None and icici.converted_csv is None
    assert sbi.problem and "password" in sbi.problem and not plan.ok

    unlocked = preview(
        conn, [StatementInput(locked, "SBI", "sbi.pdf", password="pw-123")], fetch_rate=no_network
    )
    assert unlocked.ok and unlocked.files[0].new > 0
    conn.close()


def test_ac_92_cli(tmp_path, capsys):
    pdf_path, out = tmp_path / "statement.pdf", tmp_path / "statement.csv"
    pdf_path.write_bytes(generate_pdf(42, months=1, password="pw-123"))
    assert (
        cli.main(
            ["--db", str(tmp_path / "x.db"), "pdf-to-csv", str(pdf_path), str(out), "--password", "pw-123"]
        )
        == 0
    )
    assert out.read_text(encoding="utf-8").startswith("Txn Date,Narration")
    assert (
        cli.main(["--db", str(tmp_path / "x.db"), "pdf-to-csv", str(pdf_path), str(out), "--password", "no"])
        == 1
    )
    assert "pw-123" not in capsys.readouterr().err

    db_path = tmp_path / "many.db"
    args = [
        "--db",
        str(db_path),
        "import-many",
        f"{pdf_path}=HDFC Savings",
        "--yes",
        "--pdf-password",
        "pw-123",
    ]
    assert cli.main(args) == 0
    conn = db.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] > 40
    conn.close()


# --- AC-93 UI -------------------------------------------------------------------------------------


def test_ac_93_uploader_accepts_pdf(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPENSES_DB", str(tmp_path / "ui.db"))
    for name in ("EXPENSES_PUBLIC", "EXPENSES_PASSPHRASE_HASH", "EXPENSES_DEMO", "EXPENSES_TLS_PROXY"):
        monkeypatch.delenv(name, raising=False)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    at.switch_page("ui/import_data.py").run()
    assert not at.exception
    uploader = at.get("file_uploader")[0].proto
    assert {".csv", ".pdf"} <= set(uploader.type) and uploader.multiple_files
