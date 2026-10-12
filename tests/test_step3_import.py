"""Step 3 — import, storage, de-duplication, currency (AC-13 to AC-22, AC-44 to AC-48)."""

import csv
import io
import logging
from datetime import date
from decimal import Decimal

import pytest

from expenses import db
from expenses.fingerprint import fingerprint
from expenses.fx import FxError, convert, parse_rate_response
from expenses.importer import (
    HeaderNotFoundError,
    extract_merchant,
    find_header,
    import_csv,
    normalize_description,
    undo_import,
)
from tools.make_synthetic import generate_csv, generate_rows


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    yield connection
    connection.close()


def no_network(currency, on):
    raise AssertionError("tests must never touch the network")


def run(conn, text, account="Test Bank", fetch_rate=no_network):
    return import_csv(conn, text, account=account, source_name="test.csv", fetch_rate=fetch_rate)


def rows_to_csv(rows):
    """(date, description, paise) rows -> amount-layout CSV text."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["Date", "Description", "Amount"])
    for d, desc, paise in rows:
        sign = "-" if paise < 0 else ""
        writer.writerow([d.isoformat(), desc, f"{sign}{abs(paise) // 100}.{abs(paise) % 100:02d}"])
    return buf.getvalue()


def stored(conn):
    return conn.execute("SELECT * FROM transactions ORDER BY id").fetchall()


# --- AC-13 header detection -------------------------------------------------


def test_ac_13_header_aliases_and_preamble():
    rows = [
        ["MY BANK statement"],
        ["Account: SYN-XXXX"],
        [],
        ["TXN  DATE", "Narration", "Withdrawal Amt.", "Deposit_Amt", "Closing Balance"],
    ]
    index, cols = find_header(rows)
    assert index == 3
    assert (cols.date, cols.description, cols.debit, cols.credit, cols.amount) == (0, 1, 2, 3, None)


def test_ac_13_amount_layout_and_amount_wins():
    _, cols = find_header([["Value Date", "Particulars", "Amount", "Debit", "Credit"]])
    assert (cols.date, cols.description, cols.amount) == (0, 1, 2)
    assert cols.debit is None and cols.credit is None


def test_ac_13_no_header_raises(conn):
    with pytest.raises(HeaderNotFoundError):
        run(conn, "foo,bar\n1,2\n")
    preamble = "x\n" * 20
    with pytest.raises(HeaderNotFoundError):
        run(conn, preamble + "Date,Description,Amount\n2026-01-01,A,-1\n")


# --- AC-14 import stores rows ------------------------------------------------


@pytest.mark.parametrize("layout", ["amount", "debit_credit"])
def test_ac_14_synthetic_import(conn, layout):
    expected = generate_rows(42, months=2)
    report = run(conn, generate_csv(42, layout, months=2))
    assert report.rows_rejected == 0
    assert report.rows_imported == len(expected) == report.rows_total

    got = stored(conn)
    assert sum(r["amount_minor"] for r in got) == sum(p for _, _, p in expected)
    salary = [r for r in got if r["description_norm"] == "NEFT SALARY ACME SYNTHETIC LTD"]
    assert salary[0]["amount_minor"] == 8_500_000 and salary[0]["txn_date"] == "2026-01-01"
    swiggy = [r for r in got if "SWIGGY" in r["description_norm"]]
    assert swiggy and all(r["merchant"] == "Swiggy" and r["amount_minor"] < 0 for r in swiggy)
    assert all(r["orig_currency"] == "INR" and r["orig_amount_minor"] == r["amount_minor"] for r in got)


# --- AC-15 bad rows ----------------------------------------------------------


def test_ac_15_bad_rows_reported(conn):
    text = (
        "Date,Description,Amount\n"  # line 1
        "2026-01-01,SECRET SHOP ONE,-100.00\n"  # line 2
        "2026-02-31,SECRET SHOP TWO,-200.00\n"  # line 3: impossible date
        "2026-01-03,SECRET SHOP THREE,-300.00\n"  # line 4
        "2026-01-04,SECRET SHOP FOUR,12.3.4\n"  # line 5: bad amount
        ",,\n"  # line 6: blank row, skipped silently
    )
    report = run(conn, text)
    assert report.rows_imported == 2
    assert report.rows_rejected == 2
    assert [line for line, _ in report.errors] == [3, 5]
    assert [reason for _, reason in report.errors] == ["invalid date", "invalid amount"]
    for _, reason in report.errors:
        assert "SECRET" not in reason and "12.3.4" not in reason and "2026-02-31" not in reason


# --- AC-16 fingerprint ---------------------------------------------------------


def test_ac_16_fingerprint_deterministic_and_normalised():
    d = date(2026, 1, 5)
    a = fingerprint("HDFC", d, "INR", -2000, normalize_description("  chai   point "), 0)
    b = fingerprint("HDFC", d, "INR", -2000, normalize_description("CHAI POINT"), 0)
    assert a == b
    assert len(a) == 64 and int(a, 16) >= 0  # sha256 hex
    base = ("HDFC", d, "INR", -2000, "CHAI POINT", 0)
    variants = [
        ("SBI", d, "INR", -2000, "CHAI POINT", 0),
        ("HDFC", date(2026, 1, 6), "INR", -2000, "CHAI POINT", 0),
        ("HDFC", d, "USD", -2000, "CHAI POINT", 0),
        ("HDFC", d, "INR", -2001, "CHAI POINT", 0),
        ("HDFC", d, "INR", -2000, "CHAI POINTS", 0),
        ("HDFC", d, "INR", -2000, "CHAI POINT", 1),
    ]
    for variant in variants:
        assert fingerprint(*variant) != fingerprint(*base)


# --- AC-17 / AC-18 / AC-19 de-duplication -----------------------------------------


def test_ac_17_reimport_adds_nothing(conn):
    text = generate_csv(42, "amount", months=1)
    first = run(conn, text)
    second = run(conn, text)
    assert first.rows_imported > 0
    assert second.rows_imported == 0
    assert second.rows_duplicate == first.rows_imported
    assert len(stored(conn)) == first.rows_imported


def test_ac_18_identical_rows_in_one_file_kept(conn):
    tea = "2026-01-05,POS 0042 CHAI POINT,-20.00\n"
    text = "Date,Description,Amount\n" + tea + tea
    report = run(conn, text)
    assert report.rows_imported == 2 and report.rows_duplicate == 0


def test_ac_18_per_account_dedupe(conn):
    text = "Date,Description,Amount\n2026-01-03,IMPS RENT,-22000.00\n"
    run(conn, text, account="HDFC")
    report = run(conn, text, account="SBI")
    assert report.rows_imported == 1  # decision Q4: same row in another account is not a duplicate


def test_ac_19_overlapping_statements(conn):
    rows = generate_rows(42, months=3)
    jan_feb = [r for r in rows if r[0].month in (1, 2)]
    feb_mar = [r for r in rows if r[0].month in (2, 3)]
    run(conn, rows_to_csv(jan_feb))
    report = run(conn, rows_to_csv(feb_mar))
    assert report.rows_duplicate == len([r for r in rows if r[0].month == 2])
    assert len(stored(conn)) == len(rows)


# --- AC-20 merchant ---------------------------------------------------------------


@pytest.mark.parametrize(
    "description, merchant",
    [
        ("UPI/SYN123456/SWIGGY/OKAXIS/Food order", "Swiggy"),
        ("UPI-UBER INDIA-SYN000001-RIDE", "Uber India"),
        ("upi/ref1/big bazaar/ybl", "Big Bazaar"),
        ("POS 4821 BIG BAZAAR", "Big Bazaar"),
        ("NEFT SALARY ACME SYNTHETIC LTD", None),
        ("ATM WDL SYN000123 SYN BRANCH", None),
    ],
)
def test_ac_20_merchant_extraction(description, merchant):
    assert extract_merchant(normalize_description(description)) == merchant


# --- AC-21 undo -------------------------------------------------------------------


def test_ac_21_undo_import(conn):
    first = run(conn, "Date,Description,Amount\n2026-01-01,A,-1.00\n2026-01-02,B,-2.00\n")
    second = run(conn, "Date,Description,Amount\n2026-01-03,C,-3.00\n")
    assert undo_import(conn, first.batch_id) == 2
    remaining = stored(conn)
    assert [r["description_norm"] for r in remaining] == ["C"]
    assert remaining[0]["batch_id"] == second.batch_id
    assert conn.execute("SELECT COUNT(*) FROM import_batches").fetchone()[0] == 1


# --- AC-22 logs ----------------------------------------------------------------------


def test_ac_22_logs_have_no_sensitive_values(conn, caplog):
    caplog.set_level(logging.INFO)
    text = (
        "Date,Description,Amount\n2026-01-01,UPI/SYN111111/SECRETMERCHANT/OKAXIS,-4321.99\n2026-13-01,X,1\n"
    )
    run(conn, text)
    assert caplog.records, "import should log a summary"
    for needle in ("SECRETMERCHANT", "Secretmerchant", "4321", "432199", "SYN111111"):
        assert needle not in caplog.text


# --- AC-44 to AC-48 currency ------------------------------------------------------------

FX_CSV = (
    "Date,Description,Amount,Currency\n"
    "2026-01-15,AWS CLOUD,-12.34,USD\n"
    "2026-01-15,LOCAL SHOP,-50.00,INR\n"
    "2026-01-15,OTHER SHOP,-60.00,\n"
    "2026-01-15,AWS CLOUD EXTRA,-1.00,usd\n"
)


def test_ac_44_converts_foreign_rows(conn):
    def fetch(currency, on):
        assert (currency, on) == ("USD", date(2026, 1, 15))
        return Decimal("90.3"), date(2026, 1, 15)

    report = run(conn, FX_CSV, fetch_rate=fetch)
    assert report.rows_imported == 4
    rows = {r["description_norm"]: r for r in stored(conn)}
    aws = rows["AWS CLOUD"]
    assert aws["amount_minor"] == -111430
    assert (aws["orig_currency"], aws["orig_amount_minor"], aws["fx_rate"]) == ("USD", -1234, "90.3")
    for name, paise in (("LOCAL SHOP", -5000), ("OTHER SHOP", -6000)):
        assert rows[name]["amount_minor"] == rows[name]["orig_amount_minor"] == paise
        assert rows[name]["orig_currency"] == "INR" and rows[name]["fx_rate"] is None


def test_ac_45_round_half_even():
    half = Decimal("0.5")
    assert convert(1, half) == 0
    assert convert(3, half) == 2
    assert convert(5, half) == 2
    assert convert(-3, half) == -2
    assert type(convert(1234, Decimal("90.3"))) is int


@pytest.mark.parametrize("failure", ["raise", "none"])
def test_ac_46_rate_failure_rejects_row_only(conn, failure):
    def fetch(currency, on):
        if failure == "raise":
            raise FxError("offline")
        return None

    report = run(conn, FX_CSV, fetch_rate=fetch)
    assert report.rows_imported == 2
    assert report.rows_rejected == 2
    assert report.errors == [(2, "exchange rate unavailable"), (5, "exchange rate unavailable")]


def test_ac_47_rates_cached(conn):
    calls = []

    def fetch(currency, on):
        calls.append((currency, on))
        return Decimal("90.3"), date(2026, 1, 15)

    run(conn, FX_CSV, fetch_rate=fetch)
    run(conn, FX_CSV, fetch_rate=fetch)
    assert calls == [("USD", date(2026, 1, 15))]
    assert db.get_fx_rate(conn, "USD", date(2026, 1, 15)) == Decimal("90.3")


def test_ac_48_parse_rate_response():
    body = b'{"amount":1.0,"base":"USD","date":"2026-01-15","rates":{"INR":90.3}}'
    rate, source_date = parse_rate_response(body)
    assert rate == Decimal("90.3") and type(rate) is Decimal
    assert source_date == date(2026, 1, 15)
    bad_bodies = [
        b"not json",
        b'{"message":"not found"}',
        b'{"date":"2026-01-15","rates":{}}',
        b'{"date":"2026-01-15","rates":{"INR":-1}}',
        b'{"date":"bad","rates":{"INR":90.3}}',
    ]
    for bad in bad_bodies:
        with pytest.raises(FxError):
            parse_rate_response(bad)
