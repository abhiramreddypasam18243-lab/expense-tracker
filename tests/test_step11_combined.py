"""Step 11 — combined multi-statement import (AC-60 to AC-65)."""

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from expenses import categorize as cat
from expenses import cli, db
from expenses.combine import CombinedImportError, StatementInput, commit, preview
from expenses.importer import import_csv, undo_import

ROOT = Path(__file__).resolve().parent.parent

HDFC = (
    "Date,Description,Amount\n"
    "2026-03-05,NEFT TO ICICI CARD,-20000.00\n"
    "2026-03-06,UPI/R1/SWIGGY/OKAXIS/lunch,-500.00\n"
    "2026-03-10,IMPS TO SBI R9,-5000.00\n"
)
ICICI = (
    "Txn Date,Narration,Withdrawal Amt,Deposit Amt\n"
    '06/03/2026,PAYMENT RECEIVED THANK YOU,,"20,000.00"\n'
    '07/03/2026,POS 7310 AMAZON RETAIL,"1,999.00",\n'
)
HDFC_OVERLAP = (
    "Date,Description,Amount\n"
    "2026-03-06,UPI/R1/SWIGGY/OKAXIS/lunch,-500.00\n"  # repeats a row of the first HDFC file
    "2026-03-20,POS 4821 BIG BAZAAR,-750.00\n"
    "2026-03-21,BAD ROW,abc\n"
)


def no_network(currency, on):
    raise AssertionError("tests must never touch the network")


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    cat.seed_defaults(connection)
    yield connection
    connection.close()


def counts(conn):
    return (
        conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0],
        conn.execute("SELECT COUNT(*) FROM import_batches").fetchone()[0],
    )


def three_files():
    return [
        StatementInput(HDFC, "HDFC Savings", "hdfc_mar.csv"),
        StatementInput(ICICI, "ICICI Card", "icici_mar.csv"),
        StatementInput(HDFC_OVERLAP, "HDFC Savings", "hdfc_late_mar.csv"),
    ]


def category_of(conn, description_norm):
    row = conn.execute(
        "SELECT c.name, t.category_source FROM transactions t JOIN categories c ON c.id = t.category_id"
        " WHERE t.description_norm = ?",
        (description_norm,),
    ).fetchone()
    return (row[0], row[1])


# --- AC-60 preview ------------------------------------------------------------------------------


def test_ac_60_preview_counts_and_writes_nothing(conn):
    import_csv(
        conn,
        "Date,Description,Amount\n2026-03-10,IMPS TO SBI R9,-5000.00\n",
        "HDFC Savings",
        "old.csv",
        fetch_rate=no_network,
    )
    before = counts(conn)

    p = preview(conn, three_files(), fetch_rate=no_network)
    summary = [(f.account, f.rows_total, f.new, f.duplicate, f.rejected, f.problem) for f in p.files]
    assert summary == [
        ("HDFC Savings", 3, 2, 1, 0, None),  # the IMPS row is already in the database
        ("ICICI Card", 2, 2, 0, 0, None),
        ("HDFC Savings", 3, 1, 1, 1, None),  # Swiggy repeats file 1 (same account); BAD ROW rejected
    ]
    assert p.files[2].errors == [(4, "invalid amount")]
    assert p.ok
    assert counts(conn) == before


# --- AC-61 transfer detection -------------------------------------------------------------------------


def test_ac_61_pairs_in_combined_set(conn):
    p = preview(conn, three_files(), fetch_rate=no_network)
    assert len(p.pairs) == 1
    pair = p.pairs[0]
    assert (pair.debit.account, pair.debit.txn_date, pair.debit.amount_minor) == (
        "HDFC Savings",
        date(2026, 3, 5),
        -2_000_000,
    )
    assert (pair.credit.account, pair.credit.txn_date, pair.credit.amount_minor) == (
        "ICICI Card",
        date(2026, 3, 6),
        2_000_000,
    )
    assert pair.days_apart == 1


def test_ac_61_matching_rules(conn):
    a = (
        "Date,Description,Amount\n"
        "2026-03-05,TO B ONE,-1000.00\n"
        "2026-03-09,TO B TWO,-1000.00\n"
        "2026-03-01,TO B FAR,-300.00\n"
    )
    b = (
        "Date,Description,Amount\n"
        "2026-03-08,FROM A LATE,1000.00\n"
        "2026-03-06,FROM A EARLY,1000.00\n"
        "2026-03-05,FROM A FAR,300.00\n"  # 4 days after TO B FAR -> no pair
    )
    same_account = "Date,Description,Amount\n2026-03-05,OUT,-777.00\n2026-03-05,IN,777.00\n"
    p = preview(
        conn,
        [
            StatementInput(a, "A", "a.csv"),
            StatementInput(b, "B", "b.csv"),
            StatementInput(same_account, "A", "a2.csv"),
        ],
        fetch_rate=no_network,
    )
    matched = sorted((x.debit.description, x.credit.description) for x in p.pairs)
    assert matched == [("TO B ONE", "FROM A EARLY"), ("TO B TWO", "FROM A LATE")]


# --- AC-62 all or nothing ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        StatementInput("no,header,here\n1,2,3\n", "SBI", "broken.csv"),
        StatementInput(HDFC, "   ", "no_account.csv"),
        StatementInput(b"\xff\xfe\x00 not utf-8", "SBI", "binary.csv"),
    ],
)
def test_ac_62_blocking_problem_imports_nothing(conn, bad):
    p = preview(conn, [StatementInput(ICICI, "ICICI Card", "icici.csv"), bad], fetch_rate=no_network)
    assert not p.ok
    assert p.files[0].problem is None and p.files[1].problem
    with pytest.raises(CombinedImportError):
        commit(conn, p)
    assert counts(conn) == (0, 0)


# --- AC-63 commit -----------------------------------------------------------------------------------------


def test_ac_63_commit_marks_confirmed_pairs_and_undoes_as_one(conn):
    p = preview(conn, three_files(), fetch_rate=no_network)
    report = commit(conn, p)  # all pairs confirmed by default
    assert [(f.imported, f.duplicate, f.rejected) for f in report.files] == [(3, 0, 0), (2, 0, 0), (1, 1, 1)]
    assert report.transfers_marked == 1
    assert counts(conn) == (6, 1)

    assert category_of(conn, "NEFT TO ICICI CARD") == ("Transfers", "manual")
    assert category_of(conn, "PAYMENT RECEIVED THANK YOU") == ("Transfers", "manual")
    cat.recategorize(conn)
    assert category_of(conn, "NEFT TO ICICI CARD") == ("Transfers", "manual")

    batch = conn.execute("SELECT * FROM import_batches").fetchone()
    assert "hdfc_mar.csv" in batch["source_name"] and "icici_mar.csv" in batch["source_name"]
    assert "HDFC Savings" in batch["account"] and "ICICI Card" in batch["account"]
    assert undo_import(conn, report.batch_id) == 6
    assert counts(conn) == (0, 0)


def test_ac_63_unticked_pair_keeps_rule_category(conn):
    p = preview(conn, three_files(), fetch_rate=no_network)
    report = commit(conn, p, confirmed=set())
    assert report.transfers_marked == 0
    assert category_of(conn, "NEFT TO ICICI CARD") == (cat.UNCATEGORISED, "default")


def test_ac_63_recreates_transfer_category_if_deleted(conn):
    cat.delete_category(conn, cat.category_id(conn, "Transfers"))
    commit(conn, preview(conn, three_files(), fetch_rate=no_network))
    assert category_of(conn, "NEFT TO ICICI CARD") == ("Transfers", "manual")


# --- AC-64 CLI ---------------------------------------------------------------------------------------------


def test_ac_64_cli_import_many(tmp_path, capsys):
    hdfc, icici = tmp_path / "hdfc.csv", tmp_path / "icici.csv"
    hdfc.write_text(HDFC, encoding="utf-8")
    icici.write_text(ICICI, encoding="utf-8")
    db_path = tmp_path / "cli.db"
    args = ["--db", str(db_path), "import-many", f"{hdfc}=HDFC Savings", f"{icici}=ICICI Card"]

    assert cli.main(args) == 0  # dry run
    out = capsys.readouterr().out
    assert "Transfer pairs" in out and "Dry run" in out
    conn = db.connect(db_path)
    assert counts(conn) == (0, 0)

    assert cli.main([*args, "--yes"]) == 0
    assert "1 transfer pair" in capsys.readouterr().out
    assert counts(conn) == (5, 1)
    assert category_of(conn, "NEFT TO ICICI CARD") == ("Transfers", "manual")
    conn.close()

    other = tmp_path / "other.db"
    assert (
        cli.main(
            ["--db", str(other), "import-many", f"{hdfc}=HDFC", f"{icici}=ICICI", "--yes", "--no-transfers"]
        )
        == 0
    )
    conn = db.connect(other)
    assert category_of(conn, "NEFT TO ICICI CARD") == (cat.UNCATEGORISED, "default")
    conn.close()

    assert cli.main(["--db", str(other), "import-many", str(hdfc)]) == 1  # no "=ACCOUNT"
    assert cli.main(["--db", str(other), "import-many", f"{tmp_path / 'missing.csv'}=X", "--yes"]) == 1


# --- AC-65 UI ---------------------------------------------------------------------------------------


def test_ac_65_import_page_has_multi_file_uploader(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPENSES_DB", str(tmp_path / "ui.db"))
    for name in ("EXPENSES_PUBLIC", "EXPENSES_PASSPHRASE_HASH", "EXPENSES_DEMO"):
        monkeypatch.delenv(name, raising=False)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    at.switch_page("ui/import_data.py").run()
    assert not at.exception
    uploaders = at.get("file_uploader")
    assert len(uploaders) == 1 and uploaders[0].proto.multiple_files
