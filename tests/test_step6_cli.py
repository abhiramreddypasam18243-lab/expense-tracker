"""Step 6 — CLI (AC-33, AC-34)."""

import subprocess
import sys
from pathlib import Path

import pytest

from expenses import cli, db
from tools.make_synthetic import generate_csv

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def paths(tmp_path):
    statement = tmp_path / "statement.csv"
    statement.write_text(generate_csv(42, "debit_credit", months=2), encoding="utf-8")
    return tmp_path / "test.db", statement


def call(db_path, *args):
    return cli.main(["--db", str(db_path), *args])


def test_ac_33_end_to_end(paths, capsys):
    db_path, statement = paths
    assert call(db_path, "import", str(statement), "--account", "Demo Bank") == 0
    out = capsys.readouterr().out
    assert "Imported" in out and "batch 1" in out

    assert call(db_path, "list", "--month", "2026-01", "--limit", "5") == 0
    rows = [line for line in capsys.readouterr().out.splitlines() if line.startswith("2026-01")]
    assert len(rows) == 5

    assert call(db_path, "list", "--category", "Salary") == 0
    assert "NEFT SALARY" in capsys.readouterr().out

    assert call(db_path, "summary", "--month", "2026-01") == 0
    out = capsys.readouterr().out
    assert "Spend" in out and "Income" in out and "₹" in out

    assert call(db_path, "anomalies") == 0
    capsys.readouterr()
    assert call(db_path, "recategorize") == 0
    assert "Recategorised" in capsys.readouterr().out

    assert call(db_path, "undo-import", "1") == 0
    assert "Removed" in capsys.readouterr().out
    conn = db.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0
    conn.close()


def test_ac_33_user_errors_exit_1(paths, capsys):
    db_path, statement = paths
    assert call(db_path, "import", str(statement.with_name("missing.csv")), "--account", "X") == 1
    assert call(db_path, "summary", "--month", "2026-13") == 1
    assert call(db_path, "undo-import", "999") == 1
    bad = statement.with_name("bad.csv")
    bad.write_text("no,header,here\n1,2,3\n", encoding="utf-8")
    assert call(db_path, "import", str(bad), "--account", "X") == 1
    err = capsys.readouterr().err
    assert "error:" in err


def test_ac_33_module_entry_point():
    result = subprocess.run(
        [sys.executable, "-m", "expenses", "--help"], cwd=ROOT, capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "import" in result.stdout and "anomalies" in result.stdout


def test_ac_34_export_round_trip(paths, tmp_path):
    db_path, statement = paths
    assert call(db_path, "import", str(statement), "--account", "Demo Bank") == 0
    exported = tmp_path / "export.csv"
    assert call(db_path, "export", str(exported)) == 0

    fresh = tmp_path / "fresh.db"
    assert call(fresh, "import", str(exported), "--account", "Demo Bank") == 0

    def amounts(path):
        conn = db.connect(path)
        rows = conn.execute(
            "SELECT txn_date, description_norm, amount_minor FROM transactions"
            " ORDER BY txn_date, description_norm, amount_minor"
        ).fetchall()
        conn.close()
        return [tuple(r) for r in rows]

    assert amounts(fresh) == amounts(db_path)
    assert len(amounts(fresh)) > 50


def test_ac_34_export_month_filter(paths, tmp_path):
    db_path, statement = paths
    call(db_path, "import", str(statement), "--account", "Demo Bank")
    exported = tmp_path / "jan.csv"
    assert call(db_path, "export", str(exported), "--month", "2026-01") == 0
    lines = exported.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("Date,Description,Amount")
    assert all(line.startswith("2026-01") for line in lines[1:])
