"""Step 9 — extras (AC-49 to AC-56)."""

import csv
import io
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from expenses import auth, cli, db, exporter, recurring
from expenses import categorize as cat
from expenses.importer import extract_merchant, import_csv, normalize_description
from tools.make_synthetic import generate_csv

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    cat.seed_defaults(connection)
    yield connection
    connection.close()


def no_network(currency, on):
    raise AssertionError("tests must never touch the network")


def run(conn, lines, account="Test Bank", fetch_rate=no_network):
    text = "Date,Description,Amount\n" + "".join(line + "\n" for line in lines)
    return import_csv(conn, text, account=account, source_name="t.csv", fetch_rate=fetch_rate)


# --- AC-49 merchant display -------------------------------------------------------------------


@pytest.mark.parametrize(
    "description, merchant",
    [
        ("POS 1102 PVR CINEMAS", "PVR Cinemas"),
        ("UPI/R1/KFC/OKAXIS", "KFC"),
        ("UPI-HDFC LIFE-R2-PREMIUM", "HDFC Life"),
        ("POS 9 MCDONALD'S", "Mcdonald's"),
        ("UPI/SYN123456/SWIGGY/OKAXIS/Food order", "Swiggy"),
        ("UPI-UBER INDIA-SYN000001-RIDE", "Uber India"),
        ("POS 4821 BIG BAZAAR", "Big Bazaar"),
    ],
)
def test_ac_49_merchant_acronyms(description, merchant):
    assert extract_merchant(normalize_description(description)) == merchant


# --- AC-50 Cash category and ATM rules ----------------------------------------------------------


def test_ac_50_cash_rules(conn):
    kinds = {c["name"]: c["kind"] for c in cat.list_categories(conn)}
    assert kinds["Cash"] == "expense"
    patterns = {r["pattern"] for r in cat.list_rules(conn)}
    assert {"ATM WDL", "ATM CASH", "CASH WDL"} <= patterns and "ATM" not in patterns
    rules = cat.list_rules(conn)
    assert cat.match_category("ATM WDL 123 MG ROAD", rules) == cat.category_id(conn, "Cash")
    assert cat.match_category("HOSPITAL TREATMENT CHARGES", rules) == cat.category_id(conn, "Health")


def test_ac_50_synthetic_has_no_uncategorised(conn):
    import_csv(conn, generate_csv(42, "amount"), account="Demo", source_name="s.csv", fetch_rate=no_network)
    uncategorised = cat.category_id(conn, cat.UNCATEGORISED)
    count = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE category_id = ?", (uncategorised,)
    ).fetchone()
    assert count[0] == 0


# --- AC-51 / AC-52 HTTPS for public mode ------------------------------------------------------------


def test_ac_51_public_requires_tls():
    good = auth.hash_passphrase("pw", iterations=1000)
    public = {"EXPENSES_PUBLIC": "1", "EXPENSES_PASSPHRASE_HASH": good}
    with pytest.raises(auth.ConfigError, match="HTTPS"):
        auth.load_config(public)
    assert auth.load_config(public, tls_enabled=True).public
    assert auth.load_config({**public, "EXPENSES_TLS_PROXY": "1"}).public
    with pytest.raises(auth.ConfigError):  # TLS alone never replaces the passphrase
        auth.load_config({"EXPENSES_PUBLIC": "1"}, tls_enabled=True)
    assert auth.load_config({}).public is False  # localhost needs neither


def test_ac_51_app_refuses_public_without_tls(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPENSES_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("EXPENSES_PUBLIC", "1")
    monkeypatch.setenv("EXPENSES_PASSPHRASE_HASH", auth.hash_passphrase("pw", iterations=1000))
    monkeypatch.delenv("EXPENSES_TLS_PROXY", raising=False)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    assert any("HTTPS" in e.value for e in at.error)
    assert len(at.text_input) == 0  # not even the lock screen is served


def test_ac_52_public_compose_override():
    text = (ROOT / "docker-compose.public.yml").read_text(encoding="utf-8")
    assert '"0.0.0.0:8501:8501"' in text and "!override" in text
    assert 'EXPENSES_PUBLIC: "1"' in text
    assert re.search(r"\$\{EXPENSES_PASSPHRASE_HASH:\?[^}]+\}", text)
    assert "STREAMLIT_SERVER_SSL_CERT_FILE" in text and "STREAMLIT_SERVER_SSL_KEY_FILE" in text
    base = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert '"127.0.0.1:8501:8501"' in base and "0.0.0.0" not in base
    for ignore_file in (".gitignore", ".dockerignore"):
        lines = (ROOT / ignore_file).read_text(encoding="utf-8").split()
        assert "certs/" in lines or "certs" in lines, ignore_file
        assert "*.pem" in lines, ignore_file
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "https" in dockerfile and "STREAMLIT_SERVER_SSL_CERT_FILE" in dockerfile


# --- AC-53 to AC-55 recurring payments -----------------------------------------------------------------

RECURRING_ROWS = [
    "2026-01-07,UPI-NETFLIX-R101-SUB,-649.00",
    "2026-02-07,UPI-NETFLIX-R102-SUB,-649.00",
    "2026-03-07,UPI-NETFLIX-R103-SUB,-649.00",
    "2026-04-07,UPI-NETFLIX-R104-SUB,-649.00",
    "2026-02-03,IMPS RENT REF1001 LANDLORD,-22000.00",
    "2026-03-03,IMPS RENT REF1002 LANDLORD,-22000.00",
    "2026-04-03,IMPS RENT REF1003 LANDLORD,-22000.00",
]
NOT_RECURRING_ROWS = [
    # variable bill (1500 is 36 % above the median)
    "2026-01-05,BILLPAY POWER CO,-1000.00",
    "2026-02-05,BILLPAY POWER CO,-1500.00",
    "2026-03-05,BILLPAY POWER CO,-1200.00",
    "2026-04-05,BILLPAY POWER CO,-900.00",
    # irregular gaps: 10 days, then 60 days
    "2026-01-01,POS 1 IRON GYM,-500.00",
    "2026-01-11,POS 1 IRON GYM,-500.00",
    "2026-03-12,POS 1 IRON GYM,-500.00",
    # only two payments
    "2026-01-15,UPI-MAGAZINE-R1-SUB,-100.00",
    "2026-02-15,UPI-MAGAZINE-R2-SUB,-100.00",
    # credits and transfers are never recurring payments
    "2026-01-01,NEFT SALARY ACME,50000.00",
    "2026-02-01,NEFT SALARY ACME,50000.00",
    "2026-03-01,NEFT SALARY ACME,50000.00",
    "2026-01-20,IMPS SELF TRANSFER T1,-5000.00",
    "2026-02-20,IMPS SELF TRANSFER T2,-5000.00",
    "2026-03-20,IMPS SELF TRANSFER T3,-5000.00",
]


def test_ac_53_detects_monthly_payments(conn):
    run(conn, RECURRING_ROWS + NOT_RECURRING_ROWS)
    found = recurring.find_recurring(conn)
    by_category = {r.category: r for r in found}
    assert set(by_category) == {"Entertainment", "Rent"}

    netflix = by_category["Entertainment"]
    assert netflix.name == "Netflix"
    assert (netflix.typical_minor, netflix.count, netflix.last_date) == (Decimal(64900), 4, date(2026, 4, 7))
    assert netflix.interval_days == 31  # gaps 31, 28, 31
    assert netflix.next_date == date(2026, 5, 8)
    assert netflix.active

    rent = by_category["Rent"]
    assert (rent.typical_minor, rent.count) == (Decimal(2_200_000), 3)
    assert rent.interval_days == 30  # gaps 28, 31 -> median 29.5 -> rounds half up to 30
    assert rent.next_date == date(2026, 5, 3)
    assert [r.category for r in found] == ["Rent", "Entertainment"]  # largest typical amount first


def test_ac_54_not_recurring_and_inactive(conn):
    run(conn, NOT_RECURRING_ROWS)
    assert recurring.find_recurring(conn) == []

    run(
        conn,
        [
            "2026-01-09,UPI-OLDSUB-R1-SUB,-99.00",
            "2026-02-09,UPI-OLDSUB-R2-SUB,-99.00",
            "2026-03-09,UPI-OLDSUB-R3-SUB,-99.00",
            "2026-06-30,POS 1 SOMETHING NEW,-10.00",
        ],
    )
    (old,) = recurring.find_recurring(conn)
    assert old.name == "Oldsub" and old.active is False


def test_ac_55_synthetic_recurring(conn, tmp_path, monkeypatch, capsys):
    import_csv(conn, generate_csv(42, "amount"), account="Demo", source_name="s.csv", fetch_rate=no_network)
    found = {r.category: r for r in recurring.find_recurring(conn)}
    assert found["Rent"].count == 6 and found["Rent"].typical_minor == 2_200_000
    spotify = [r for r in recurring.find_recurring(conn) if r.name == "Spotify"]
    assert spotify and spotify[0].count == 6 and spotify[0].typical_minor == 11_900

    db_path = tmp_path / "cli.db"
    statement = tmp_path / "s.csv"
    statement.write_text(generate_csv(42, "amount"), encoding="utf-8")
    assert cli.main(["--db", str(db_path), "import", str(statement), "--account", "Demo"]) == 0
    capsys.readouterr()
    assert cli.main(["--db", str(db_path), "recurring"]) == 0
    out = capsys.readouterr().out
    assert "Spotify" in out and "₹22,000.00" in out

    monkeypatch.setenv("EXPENSES_DB", str(db_path))
    for name in ("EXPENSES_PUBLIC", "EXPENSES_PASSPHRASE_HASH", "EXPENSES_DEMO"):
        monkeypatch.delenv(name, raising=False)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    at.switch_page("ui/insights_page.py").run()
    assert not at.exception
    page_text = "\n".join([m.value for m in at.markdown] + [c.value for c in at.caption])
    assert "Recurring payments" in page_text


# --- AC-56 original currency in export ----------------------------------------------------------------


def test_ac_56_export_keeps_original_currency(conn):
    def fetch(currency, on):
        return Decimal("90.3"), on

    text = (
        "Date,Description,Amount,Currency\n2026-01-15,AWS CLOUD,-12.34,USD\n2026-01-16,LOCAL SHOP,-50.00,\n"
    )
    import_csv(conn, text, account="Card", source_name="t.csv", fetch_rate=fetch)
    exported = exporter.export_csv(conn)
    rows = list(csv.DictReader(io.StringIO(exported)))
    aws = next(r for r in rows if r["Description"] == "AWS CLOUD")
    assert (aws["Amount"], aws["Original Currency"], aws["Original Amount"]) == ("-1114.30", "USD", "-12.34")
    shop = next(r for r in rows if r["Description"] == "LOCAL SHOP")
    assert (shop["Original Currency"], shop["Original Amount"]) == ("INR", "-50.00")

    fresh = db.connect(":memory:")
    import_csv(fresh, exported, account="Card", source_name="export.csv", fetch_rate=no_network)
    amounts = sorted(r[0] for r in fresh.execute("SELECT amount_minor FROM transactions"))
    assert amounts == [-111430, -5000]
    fresh.close()


# --- AC-57 anomalies must also be materially larger (decision after Step 9) -----------------------


def test_ac_57_two_times_rule(conn):
    from expenses.anomaly import find_anomalies, robust_scores

    # 12 x ₹649 subscription (< half of 25), 6 x ₹119, 7 cinema tickets -> MAD is only ₹28.
    tickets = [677, 700, 720, 800, 1141, 1200, 2000]
    values = [649] * 12 + [119] * 6 + tickets
    lines = [f"2026-01-{i % 28 + 1:02d},POS {i} PVR CINEMAS,-{v}.00" for i, v in enumerate(values)]
    run(conn, lines)

    scores = dict(zip(values, robust_scores([v * 100 for v in values]), strict=True))
    assert scores[800] > 3.5  # statistically "unusual" on its own...
    found = find_anomalies(conn)
    assert [a.amount_minor for a in found] == [-200_000]  # ...but only ₹2,000 is >= 2 x typical ₹649
