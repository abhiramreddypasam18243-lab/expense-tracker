"""Step 26 — transactions added by hand and the cash wallet (AC-131 to AC-135)."""

import sqlite3
from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from expenses import categorize, cli, db, insights, manual
from expenses.importer import import_csv, undo_import

ROOT = Path(__file__).resolve().parent.parent
TODAY = date(2026, 10, 28)


def no_network(currency, on):
    raise AssertionError("no network in tests")


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    categorize.seed_defaults(c)
    yield c
    c.close()


STATEMENT = "\n".join(
    [
        "Date,Description,Amount",
        "2026-10-01,SALARY OCT,50000.00",
        "2026-10-03,ATM WDL 123 MG ROAD,-5000.00",
        "2026-10-05,UPI/SWIGGY/123,-400.00",
    ]
)


def cat(conn, name):
    return categorize.category_id(conn, name)


# --- AC-131 adding entries ---------------------------------------------------------------------


def test_ac_131_add_with_chosen_or_automatic_category(conn):
    chosen = manual.add(
        conn, date(2026, 10, 4), -2_000, "Tea at canteen", category_id=cat(conn, "Food & Dining")
    )
    auto = manual.add(conn, date(2026, 10, 4), -30_000, "  swiggy   dinner ", "HDFC Card", today=TODAY)
    a, b = db.get_transaction(conn, chosen), db.get_transaction(conn, auto)
    assert (a["account"], a["amount_minor"], a["category_name"], a["category_source"]) == (
        "Cash",
        -2_000,
        "Food & Dining",
        "manual",
    )
    assert (b["description"], b["description_norm"], b["account"]) == (
        "swiggy dinner",
        "SWIGGY DINNER",
        "HDFC Card",
    )
    assert (b["category_name"], b["category_source"]) == ("Food & Dining", "rule")
    assert a["is_manual"] == 1 and a["orig_currency"] == "INR" and a["orig_amount_minor"] == -2_000


def test_ac_131_identical_entries_are_both_kept(conn):
    for _ in range(2):
        manual.add(conn, date(2026, 10, 4), -2_000, "Tea", today=TODAY)
    assert len(db.list_transactions(conn)) == 2


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"amount_minor": 0}, "amount"),
        ({"description": "   "}, "description"),
        ({"description": "x" * 201}, "200"),
        ({"account": ""}, "paid"),
        ({"txn_date": date(2026, 10, 29)}, "future"),
        ({"category_id": 9999}, "category"),
    ],
)
def test_ac_131_invalid_entries_are_refused(conn, kwargs, message):
    args = {"txn_date": date(2026, 10, 4), "amount_minor": -100, "description": "Tea", "account": "Cash"}
    with pytest.raises(manual.EntryError, match=message):
        manual.add(conn, **{**args, **kwargs}, today=TODAY)
    assert db.list_transactions(conn) == []


# --- AC-132 a separate batch: never undone, only these rows can be changed ---------------------


def test_ac_132_entries_survive_undo_and_are_not_an_import(conn):
    report = import_csv(conn, STATEMENT, account="HDFC", source_name="s.csv", fetch_rate=no_network)
    manual.add(conn, date(2026, 10, 4), -2_000, "Tea", today=TODAY)
    assert [b["id"] for b in db.list_batches(conn)] == [report.batch_id]
    undo_import(conn, report.batch_id)
    assert [r["description"] for r in db.list_transactions(conn)] == ["Tea"]


def test_ac_132_edit_and_delete_only_entries_added_by_hand(conn):
    import_csv(conn, STATEMENT, account="HDFC", source_name="s.csv", fetch_rate=no_network)
    imported = db.list_transactions(conn)[0]["id"]
    entry = manual.add(conn, date(2026, 10, 4), -2_000, "Tea", today=TODAY)
    manual.update(
        conn, entry, date(2026, 10, 6), 150_000, "Gift from Amma", "Cash", cat(conn, "Salary"), TODAY
    )
    row = db.get_transaction(conn, entry)
    assert (row["txn_date"], row["amount_minor"], row["orig_amount_minor"], row["description"]) == (
        "2026-10-06",
        150_000,
        150_000,
        "Gift from Amma",
    )
    assert (row["category_name"], row["category_source"]) == ("Salary", "manual")
    with pytest.raises(manual.EntryError):
        manual.update(conn, imported, date(2026, 10, 6), -1, "x", "Cash", today=TODAY)
    with pytest.raises(manual.EntryError):
        manual.delete(conn, imported)
    assert manual.is_manual(conn, entry) and not manual.is_manual(conn, imported)
    manual.delete(conn, entry)
    assert db.get_transaction(conn, entry) is None and db.get_transaction(conn, imported) is not None


# --- AC-133 the cash wallet ---------------------------------------------------------------------


def test_ac_133_atm_is_a_move_to_cash_and_logged_cash_is_spending(conn):
    import_csv(conn, STATEMENT, account="HDFC", source_name="s.csv", fetch_rate=no_network)
    assert insights.monthly_summary(conn, "2026-10").spend_minor == 40_000  # Swiggy only, not the ATM
    assert manual.cash_gap(conn, "2026-10") == manual.CashGap(500_000, 0)
    manual.add(
        conn, date(2026, 10, 4), -120_000, "Vegetables", category_id=cat(conn, "Groceries"), today=TODAY
    )
    manual.add(conn, date(2026, 10, 4), -30_000, "Card snack", "HDFC Card", today=TODAY)  # not cash
    summary = insights.monthly_summary(conn, "2026-10")
    assert summary.spend_minor == 40_000 + 120_000 + 30_000
    assert ("Groceries", 120_000) in [(c.name, c.spend_minor) for c in summary.by_category]
    gap = manual.cash_gap(conn, "2026-10")
    assert (gap.withdrawn_minor, gap.logged_minor, gap.unlogged_minor) == (500_000, 120_000, 380_000)
    assert manual.CashGap(100, 300).unlogged_minor == 0


def test_ac_133_older_database_is_upgraded_once(tmp_path):
    path = tmp_path / "v2.db"
    conn = db.connect(path)
    categorize.seed_defaults(conn)
    conn.execute("UPDATE categories SET kind = 'expense' WHERE name = 'Cash'")
    conn.commit()
    conn.close()
    raw = sqlite3.connect(path)  # make it look like a version-2 database
    raw.executescript("""ALTER TABLE import_batches DROP COLUMN manual; PRAGMA user_version = 2;""")
    raw.close()
    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
    assert "manual" in {r["name"] for r in conn.execute("PRAGMA table_info(import_batches)")}
    assert db.get_category(conn, cat(conn, "Cash"))["kind"] == "transfer"
    conn.execute("UPDATE categories SET kind = 'expense' WHERE name = 'Cash'")  # the owner changes it back
    conn.commit()
    conn.close()
    conn = db.connect(path)
    assert db.get_category(conn, cat(conn, "Cash"))["kind"] == "expense"  # kept: only migrated once
    conn.close()


def test_ac_133_transfer_matching_never_uses_cash():
    from expenses import combine

    c = db.connect(":memory:")
    categorize.seed_defaults(c)
    assert combine._transfer_category(c) == cat(c, "Transfers")


# --- AC-134 UI ------------------------------------------------------------------------------------


def page(monkeypatch, tmp_path, path, seed=None):
    monkeypatch.setenv("EXPENSES_DB", str(tmp_path / "ui.db"))
    monkeypatch.setenv("EXPENSES_DEMO", "0")
    for name in ("EXPENSES_PUBLIC", "EXPENSES_PASSPHRASE_HASH", "EXPENSES_TLS_PROXY"):
        monkeypatch.delenv(name, raising=False)
    if seed:
        c = db.connect(tmp_path / "ui.db")
        categorize.seed_defaults(c)
        seed(c)
        c.close()
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    at.switch_page(path).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_ac_134_add_from_an_empty_ledger(monkeypatch, tmp_path):
    at = page(monkeypatch, tmp_path, "ui/transactions.py")
    at.button(key="manual-add").click().run()
    at.text_input(key="me-new-amount").set_value("1,200.50")
    at.text_input(key="me-new-desc").set_value("Vegetables at the market")
    at.selectbox(key="me-new-cat-expense").set_value("Groceries")
    at.button(key="me-new-save").click().run()
    assert not at.exception, [e.value for e in at.exception]
    c = db.connect(tmp_path / "ui.db")
    rows = db.list_transactions(c)
    assert [(r["description"], r["amount_minor"], r["category_name"], r["account"]) for r in rows] == [
        ("Vegetables at the market", -120_050, "Groceries", "Cash")
    ]


def test_ac_134_bad_amount_shows_an_error(monkeypatch, tmp_path):
    at = page(monkeypatch, tmp_path, "ui/transactions.py")
    at.button(key="manual-add").click().run()
    at.text_input(key="me-new-amount").set_value("abc")
    at.text_input(key="me-new-desc").set_value("Tea")
    at.button(key="me-new-save").click().run()
    assert any("amount" in e.value for e in at.error)
    c = db.connect(tmp_path / "ui.db")
    assert db.list_transactions(c) == []


def test_ac_134_edit_entry_and_dashboard_cash_notice(monkeypatch, tmp_path):
    def seed(c):
        import_csv(c, STATEMENT, account="HDFC", source_name="s.csv", fetch_rate=no_network)
        manual.add(c, date(2026, 10, 4), -120_000, "Vegetables")

    at = page(monkeypatch, tmp_path, "ui/transactions.py", seed)
    assert at.selectbox(key="manual-pick").options and len(at.selectbox(key="manual-pick").options) == 1
    at.button(key="manual-edit").click().run()
    entry_id = db.list_transactions(db.connect(tmp_path / "ui.db"), search="vegetables")[0]["id"]
    at.text_input(key=f"me-{entry_id}-amount").set_value("1500")
    at.button(key=f"me-{entry_id}-save").click().run()
    assert not at.exception
    c = db.connect(tmp_path / "ui.db")
    assert db.get_transaction(c, entry_id)["amount_minor"] == -150_000

    at.switch_page("ui/dashboard.py").run()
    notice = " ".join(i.value for i in at.info)
    assert "₹5,000.00" in notice and "₹3,500.00" in notice  # withdrew 5,000, logged 1,500


# --- AC-135 CLI -----------------------------------------------------------------------------------


def test_ac_135_cli_add_and_delete(tmp_path, capsys):
    path = str(tmp_path / "cli.db")
    assert (
        cli.main(["--db", path, "add", "--spent", "250", "--description", "Tea", "--date", "2026-10-04"]) == 0
    )
    assert "Added #1" in capsys.readouterr().out
    assert (
        cli.main(["--db", path, "add", "--received", "2000", "--description", "Gift", "--category", "Salary"])
        == 0
    )
    assert cli.main(["--db", path, "add", "--spent", "-5", "--description", "x"]) == 1
    assert cli.main(["--db", path, "add", "--spent", "5", "--description", "x", "--category", "Nope"]) == 1
    conn = db.connect(path)
    rows = {r["description"]: r for r in db.list_transactions(conn)}
    assert rows["Tea"]["amount_minor"] == -25_000 and rows["Tea"]["account"] == "Cash"
    assert rows["Gift"]["category_name"] == "Salary"
    conn.close()
    assert cli.main(["--db", path, "delete-entry", str(rows["Tea"]["id"])]) == 0
    assert cli.main(["--db", path, "delete-entry", "999"]) == 1
