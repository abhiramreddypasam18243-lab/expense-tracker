"""Step 22 — learned categorisation (AC-112 to AC-116)."""

import logging
import sqlite3

import pytest

from expenses import categorize as cat
from expenses import cli, db, learn
from expenses.importer import import_csv
from tools.evaluate_model import KINDS, NAMES, examples, vocabulary_breakdown
from tools.make_synthetic import generate_labelled

UNSEEN = "ROYAL LOTUS BIRYANI HOUSE"


def no_network(currency, on):
    raise AssertionError("tests must never touch the network")


def labelled_db(path=":memory:", source="manual", seed=7):
    """A database whose transactions were categorised by hand (synthetic benchmark merchants)."""
    conn = db.connect(path)
    cat.seed_defaults(conn)
    ids = {r["name"]: r["id"] for r in db.list_categories(conn)}
    rows = [r for r in generate_labelled(seed) if r[0] != UNSEEN]
    with conn:
        batch = db.create_batch(conn, "s.csv", "Bank")
        for i, (merchant, text, category, paise) in enumerate(rows):
            db.insert_transaction(
                conn,
                {
                    "account": "Bank",
                    "txn_date": "2026-03-01",
                    "description": text,
                    "description_norm": text.upper(),
                    "merchant": merchant,
                    "amount_minor": paise,
                    "orig_currency": "INR",
                    "orig_amount_minor": paise,
                    "fx_rate": None,
                    "category_id": ids[category],
                    "category_source": source,
                    "fingerprint": f"fp-{i}",
                    "batch_id": batch,
                },
            )
    return conn, ids


NEW_STATEMENT = (
    "Date,Description,Amount\n"
    f"2026-04-01,UPI/SYN000001/{UNSEEN}/OKAXIS/Pay,-540.00\n"
    "2026-04-02,UPI/SYN000002/SWIGGY/OKAXIS/Food order,-310.00\n"
    "2026-04-03,POS 4821 QWZX PLOKM,-75.00\n"
)


def rows_of(conn):
    return conn.execute(
        """SELECT t.description_norm, c.name, t.category_source, t.category_confidence
           FROM transactions t JOIN categories c ON c.id = t.category_id WHERE t.txn_date >= '2026-04-01'
           ORDER BY t.txn_date"""
    ).fetchall()


# --- AC-112 migration -------------------------------------------------------------------------------


def test_ac_112_version_1_database_is_migrated(tmp_path):
    v1 = db._SCHEMA.replace("'default', 'model'", "'default'")
    v1 = v1.replace(
        "    created_at        TEXT NOT NULL,\n"
        "    category_confidence REAL  -- set when category_source = 'model' (0..1)\n",
        "    created_at        TEXT NOT NULL\n",
    )
    v1 = (
        v1[: v1.index("CREATE TABLE IF NOT EXISTS app_settings")]
        + v1[v1.index("CREATE TABLE IF NOT EXISTS fx_rates") :]
    )
    assert "category_confidence" not in v1 and "'model'" not in v1
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.executescript(v1)
    raw.execute(
        "INSERT INTO categories (id, name, kind, color) VALUES (1, 'Food & Dining', 'expense', '#000')"
    )
    raw.execute(
        "INSERT INTO import_batches (id, source_name, account, imported_at) "
        "VALUES (1, 'a.csv', 'Bank', '2026-01-01')"
    )
    raw.execute(
        """INSERT INTO transactions (id, account, txn_date, description, description_norm, merchant,
           amount_minor, orig_amount_minor, category_id, category_source, fingerprint, batch_id, created_at)
           VALUES (41, 'Bank', '2026-01-02', 'Swiggy', 'SWIGGY', 'SWIGGY', -500, -500, 1, 'rule',
                   'fp-41', 1, 'x')"""
    )
    raw.execute("PRAGMA user_version = 1")
    raw.commit()
    raw.close()

    conn = db.connect(path)
    row = conn.execute("SELECT id, fingerprint, category_source, amount_minor FROM transactions").fetchone()
    assert tuple(row) == (41, "fp-41", "rule", -500)
    db.set_transaction_category(conn, 41, 1, "model", 0.91)
    conn.commit()
    assert conn.execute("SELECT category_confidence FROM transactions").fetchone()[0] == pytest.approx(0.91)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    db.set_setting(conn, "model_enabled", "0")
    conn.close()

    again = db.connect(path)  # second open: nothing to migrate, data intact
    assert again.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1
    assert db.get_setting(again, "model_enabled") == "0"
    again.close()


# --- AC-113 accuracy on the synthetic benchmark -------------------------------------------------------


def test_ac_113_unseen_merchants_known_vocabulary():
    breakdown = vocabulary_breakdown(7)
    correct, total = breakdown["known word"]
    assert total >= 50 and correct / total >= 0.95

    ev = learn.evaluate(examples(7), KINDS, NAMES)
    assert ev.accuracy_at_threshold is not None and ev.accuracy_at_threshold >= 0.90

    model = learn.train(examples(7), KINDS)
    label, confidence = model.predict(f"UPI/SYN1/{UNSEEN}/OKAXIS/PAY", -54000)
    assert label == "Food & Dining" and confidence >= learn.DEFAULT_THRESHOLD


def test_ac_113_sign_constraint():
    model = learn.train(examples(7), KINDS)
    texts = [f"NEFT {UNSEEN}", "UPI/SYN9/ROYAL LOTUS PAYROLL/YBL", "POS 1 GOLDEN SALARY KITCHEN"]
    for text in texts:
        credit = model.predict(text, 50000)
        debit = model.predict(text, -50000)
        assert KINDS[credit[0]] == "income"
        assert KINDS[debit[0]] == "expense"


# --- AC-114 import and re-run -------------------------------------------------------------------------


def test_ac_114_import_uses_model_after_rules():
    conn, ids = labelled_db()
    import_csv(conn, NEW_STATEMENT, "Bank", "april.csv", fetch_rate=no_network)
    biryani, swiggy, nonsense = rows_of(conn)
    assert (biryani["name"], biryani["category_source"]) == ("Food & Dining", "model")
    assert biryani["category_confidence"] >= learn.DEFAULT_THRESHOLD
    assert (swiggy["name"], swiggy["category_source"], swiggy["category_confidence"]) == (
        "Food & Dining",
        "rule",
        None,
    )
    if nonsense["category_source"] == "model":  # only ever when confident enough
        assert nonsense["category_confidence"] >= learn.DEFAULT_THRESHOLD
    else:
        assert (nonsense["name"], nonsense["category_source"]) == ("Uncategorised", "default")

    labelled = {r["description_norm"] for r in db.labelled_rows(conn)}
    assert biryani["description_norm"] not in labelled  # the model never trains on its own output
    conn.close()


def test_ac_114_rerun_keeps_manual_and_respects_threshold_and_switch():
    conn, ids = labelled_db()
    learn.set_enabled(conn, False)
    import_csv(conn, NEW_STATEMENT, "Bank", "april.csv", fetch_rate=no_network)
    assert [r["category_source"] for r in rows_of(conn)] == ["default", "rule", "default"]

    manual_before = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE category_source = 'manual'"
    ).fetchone()[0]
    learn.set_enabled(conn, True)
    cat.recategorize(conn)
    assert rows_of(conn)[0]["category_source"] == "model"
    manual_after = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE category_source = 'manual'"
    ).fetchone()[0]
    assert manual_after == manual_before

    def model_rows():
        return conn.execute("SELECT COUNT(*) FROM transactions WHERE category_source = 'model'").fetchone()[0]

    learn.set_threshold(conn, 0.40)
    cat.recategorize(conn)
    loose = model_rows()
    learn.set_threshold(conn, 0.95)
    cat.recategorize(conn)
    assert model_rows() <= loose
    with pytest.raises(ValueError):
        learn.set_threshold(conn, 0.2)

    learn.set_enabled(conn, False)
    cat.recategorize(conn)  # switched off: earlier model labels go back to Uncategorised
    assert model_rows() == 0
    conn.close()


def test_ac_114_not_enough_examples():
    conn = db.connect(":memory:")
    cat.seed_defaults(conn)
    import_csv(conn, NEW_STATEMENT, "Bank", "april.csv", fetch_rate=no_network)
    assert learn.predictor(conn) is None  # only 1 rule-labelled row: not ready
    assert "model" not in [r["category_source"] for r in rows_of(conn)]
    conn.close()


# --- AC-115 evaluation ----------------------------------------------------------------------------------


def test_ac_115_evaluation_report():
    ev = learn.evaluate(examples(7), KINDS, NAMES)
    assert ev.shared_merchants == 0 and ev.n_train + ev.n_test == len(examples(7))
    assert 0 < ev.accuracy <= 1 and 0 < ev.macro_f1 <= 1
    assert ev.per_category and all(0 <= s.precision <= 1 and 0 <= s.recall <= 1 for s in ev.per_category)
    assert len(ev.confusion) == len(ev.confusion_labels) == len(ev.confusion[0])
    coverages = [cov for _, cov, _ in ev.thresholds]
    assert coverages == sorted(coverages, reverse=True)  # stricter threshold -> fewer rows labelled
    assert ev.curve[-1][0] == ev.n_train and len(ev.curve) >= 3

    conn, _ = labelled_db()
    assert learn.evaluate_db(conn).n_test > 0  # the user's own data (here: manual labels)
    conn.close()

    with pytest.raises(learn.NotEnoughData):
        learn.evaluate(examples(7)[:10], KINDS, NAMES)


# --- AC-116 CLI ---------------------------------------------------------------------------------------------


def test_ac_116_cli_model_report(tmp_path, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    empty = tmp_path / "empty.db"
    assert cli.main(["--db", str(empty), "model-report"]) == 0
    assert "Not enough data" in capsys.readouterr().out

    path = tmp_path / "labelled.db"
    labelled_db(path).__getitem__(0).close()
    assert cli.main(["--db", str(path), "model-report"]) == 0
    out = capsys.readouterr().out
    assert "Accuracy on unseen merchants" in out and "Learning curve" in out
    assert UNSEEN not in caplog.text and "KITCHEN" not in caplog.text
