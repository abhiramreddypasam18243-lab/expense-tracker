"""Step 4 — categorisation (AC-23 to AC-26)."""

import pytest

from expenses import categorize as cat
from expenses import db
from expenses.importer import import_csv
from tools.make_synthetic import generate_csv


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    cat.seed_defaults(connection)
    yield connection
    connection.close()


def no_network(currency, on):
    raise AssertionError("tests must never touch the network")


def run(conn, text):
    return import_csv(conn, text, account="Test Bank", source_name="t.csv", fetch_rate=no_network)


def category_of(conn, description_norm):
    row = conn.execute(
        "SELECT c.name, t.category_source FROM transactions t JOIN categories c ON c.id = t.category_id"
        " WHERE t.description_norm = ?",
        (description_norm,),
    ).fetchone()
    return (row[0], row[1]) if row else None


def test_ac_23_seed_once(conn):
    # the fixture already seeded once; seeding again must change nothing
    first = (len(cat.list_categories(conn)), len(cat.list_rules(conn)))
    cat.seed_defaults(conn)
    assert (len(cat.list_categories(conn)), len(cat.list_rules(conn))) == first
    names = {c["name"] for c in cat.list_categories(conn)}
    assert {"Food & Dining", "Salary", "Transfers", cat.UNCATEGORISED} <= names
    assert first[1] >= 25
    kinds = {c["name"]: c["kind"] for c in cat.list_categories(conn)}
    assert kinds["Salary"] == "income" and kinds["Transfers"] == "transfer" and kinds["Rent"] == "expense"


def test_ac_23_user_deletions_are_not_reseeded(conn):
    cat.delete_category(conn, cat.category_id(conn, "Travel"))
    cat.seed_defaults(conn)
    assert cat.category_id(conn, "Travel") is None


def test_ac_24_first_match_by_priority(conn):
    food = cat.category_id(conn, "Food & Dining")
    shopping = cat.category_id(conn, "Shopping")
    rules = [
        {"id": 2, "pattern": "PIZZA", "category_id": food, "priority": 50},
        {"id": 1, "pattern": "PIZZA HUT", "category_id": shopping, "priority": 10},
        {"id": 3, "pattern": "TIE", "category_id": food, "priority": 5},
        {"id": 4, "pattern": "TIE", "category_id": shopping, "priority": 5},
    ]
    assert cat.match_category("POS 1 PIZZA HUT", rules) == shopping
    assert cat.match_category("POS 1 PIZZA CORNER", rules) == food
    assert cat.match_category("TIE BREAK", rules) == food  # same priority -> lower id wins
    assert cat.match_category("NOTHING HERE", rules) is None


def test_ac_24_import_applies_rules(conn):
    run(conn, generate_csv(42, "amount", months=1))
    assert category_of(conn, "NEFT SALARY ACME SYNTHETIC LTD") == ("Salary", "rule")
    swiggy = conn.execute(
        "SELECT DISTINCT c.name FROM transactions t JOIN categories c ON c.id = t.category_id"
        " WHERE t.description_norm LIKE '%SWIGGY%'"
    ).fetchall()
    assert [r[0] for r in swiggy] == ["Food & Dining"]
    run(
        conn, "Date,Description,Amount\n2026-01-01,REFUND AMAZON RETAIL SYN1,150.00\n2026-01-02,XYZZY,-5.00\n"
    )
    assert category_of(conn, "REFUND AMAZON RETAIL SYN1") == ("Refunds", "rule")  # beats AMAZON -> Shopping
    assert category_of(conn, "XYZZY") == (cat.UNCATEGORISED, "default")


def test_ac_25_manual_override_survives_rerun(conn):
    run(conn, "Date,Description,Amount\n2026-01-01,ATM WDL ONE,-500.00\n2026-01-02,ATM WDL TWO,-600.00\n")
    one = conn.execute("SELECT id FROM transactions WHERE description_norm = 'ATM WDL ONE'").fetchone()[0]
    cat.set_category(conn, one, cat.category_id(conn, "Travel"))
    assert category_of(conn, "ATM WDL ONE") == ("Travel", "manual")

    cat.add_rule(conn, "ATM WDL", cat.category_id(conn, "Transfers"), priority=1)
    changed = cat.recategorize(conn)
    assert changed == 1
    assert category_of(conn, "ATM WDL ONE") == ("Travel", "manual")
    assert category_of(conn, "ATM WDL TWO") == ("Transfers", "rule")


def test_ac_26_delete_category_moves_to_uncategorised(conn):
    run(conn, "Date,Description,Amount\n2026-01-01,UPI-NETFLIX-SYN1-SUB,-649.00\n")
    assert category_of(conn, "UPI-NETFLIX-SYN1-SUB") == ("Entertainment", "rule")
    entertainment = cat.category_id(conn, "Entertainment")
    cat.delete_category(conn, entertainment)
    assert category_of(conn, "UPI-NETFLIX-SYN1-SUB") == (cat.UNCATEGORISED, "default")
    assert all(r["category_id"] != entertainment for r in cat.list_rules(conn))
    with pytest.raises(ValueError):
        cat.delete_category(conn, cat.category_id(conn, cat.UNCATEGORISED))
