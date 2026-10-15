"""Step 20 — remembered, encrypted PDF passwords per account (AC-105 to AC-107)."""

import logging

from expenses import categorize as cat
from expenses import cli, db, vault
from expenses.combine import StatementInput, commit, preview
from tools.make_synthetic import generate_pdf

PASSWORD = "Dob01011990"


def no_network(currency, on):
    raise AssertionError("tests must never touch the network")


def fresh(tmp_path, name="x.db"):
    conn = db.connect(tmp_path / name)
    cat.seed_defaults(conn)
    return conn


# --- AC-105 vault -----------------------------------------------------------------------------------


def test_ac_105_round_trip_encrypted_forget(tmp_path):
    conn = fresh(tmp_path)
    vault.save(conn, "HDFC Savings", PASSWORD)
    vault.save(conn, "SBI Savings", "other-1")
    assert vault.get(conn, "HDFC Savings") == PASSWORD
    assert vault.get(conn, "ICICI") is None
    assert vault.accounts(conn) == ["HDFC Savings", "SBI Savings"]

    vault.save(conn, "HDFC Savings", "changed-2")  # replaces
    assert vault.get(conn, "HDFC Savings") == "changed-2"

    stored = b"".join(bytes(r[0]) for r in conn.execute("SELECT token FROM pdf_passwords"))
    assert b"changed-2" not in stored and b"other-1" not in stored  # never plaintext in the database
    assert (tmp_path / "x.db").read_bytes().find(b"changed-2") == -1

    assert vault.forget(conn, "SBI Savings") is True
    assert vault.forget(conn, "SBI Savings") is False
    vault.forget_all(conn)
    assert vault.accounts(conn) == []
    conn.close()


def test_ac_105_key_file_reused_and_lost_key_reads_as_absent(tmp_path):
    conn = fresh(tmp_path)
    vault.save(conn, "HDFC", PASSWORD)
    key_file = tmp_path / vault.KEY_FILE_NAME
    key = key_file.read_bytes()
    conn.close()

    conn = db.connect(tmp_path / "x.db")
    assert vault.get(conn, "HDFC") == PASSWORD and key_file.read_bytes() == key  # same key reused
    conn.close()

    key_file.unlink()  # key lost: the database alone can't reveal the password
    conn = db.connect(tmp_path / "x.db")
    assert vault.get(conn, "HDFC") is None
    conn.close()


def test_ac_105_in_memory_database_uses_memory_key(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    conn = db.connect(":memory:")
    vault.save(conn, "HDFC", PASSWORD)
    assert vault.get(conn, "HDFC") == PASSWORD
    assert not list(tmp_path.glob("*.key"))
    conn.close()


# --- AC-106 combined import --------------------------------------------------------------------------


def test_ac_106_saved_on_commit_then_used_automatically(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    conn = fresh(tmp_path)
    march, april = generate_pdf(1, months=1, password=PASSWORD), generate_pdf(2, months=1, password=PASSWORD)

    plan = preview(conn, [StatementInput(march, "HDFC", "m.pdf", password=PASSWORD)], fetch_rate=no_network)
    assert plan.ok and not plan.files[0].used_saved_password
    assert vault.get(conn, "HDFC") is None  # nothing saved by a preview
    commit(conn, plan)
    assert vault.get(conn, "HDFC") == PASSWORD

    again = preview(conn, [StatementInput(april, "HDFC", "a.pdf")], fetch_rate=no_network)
    assert again.ok and again.files[0].used_saved_password and again.files[0].new > 0

    other = preview(conn, [StatementInput(april, "SBI", "a.pdf")], fetch_rate=no_network)
    assert "password-protected" in other.files[0].problem  # saved per account, not shared

    assert PASSWORD not in caplog.text
    conn.close()


def test_ac_106_wrong_saved_password_and_replacement(tmp_path):
    conn = fresh(tmp_path)
    vault.save(conn, "HDFC", "old-password")
    pdf = generate_pdf(3, months=1, password=PASSWORD)

    plan = preview(conn, [StatementInput(pdf, "HDFC", "s.pdf")], fetch_rate=no_network)
    problem = plan.files[0].problem
    assert "saved password for HDFC" in problem and "old-password" not in problem and not plan.ok

    fixed = preview(conn, [StatementInput(pdf, "HDFC", "s.pdf", password=PASSWORD)], fetch_rate=no_network)
    commit(conn, fixed)
    assert vault.get(conn, "HDFC") == PASSWORD
    conn.close()


def test_ac_106_unticked_or_failed_saves_nothing(tmp_path):
    conn = fresh(tmp_path)
    pdf = generate_pdf(4, months=1, password=PASSWORD)
    plan = preview(
        conn, [StatementInput(pdf, "HDFC", "s.pdf", password=PASSWORD, remember=False)], fetch_rate=no_network
    )
    commit(conn, plan)
    assert vault.get(conn, "HDFC") is None

    wrong = preview(conn, [StatementInput(pdf, "SBI", "s.pdf", password="nope")], fetch_rate=no_network)
    assert not wrong.ok
    assert vault.get(conn, "SBI") is None
    conn.close()


# --- AC-107 CLI ------------------------------------------------------------------------------------------


def test_ac_107_cli_uses_saved_and_forgets(tmp_path, capsys):
    db_path = tmp_path / "cli.db"
    first, second = tmp_path / "m.pdf", tmp_path / "a.pdf"
    first.write_bytes(generate_pdf(5, months=1, password=PASSWORD))
    second.write_bytes(generate_pdf(6, months=1, password=PASSWORD))
    base = ["--db", str(db_path), "import-many"]
    assert cli.main([*base, f"{first}=HDFC", "--yes", "--pdf-password", PASSWORD]) == 0
    assert cli.main([*base, f"{second}=HDFC", "--yes"]) == 0  # no password typed: the saved one opens it
    assert "password-protected" not in capsys.readouterr().out

    assert cli.main(["--db", str(db_path), "forget-password", "HDFC"]) == 0
    assert cli.main(["--db", str(db_path), "forget-password", "HDFC"]) == 1
    output = capsys.readouterr()
    assert PASSWORD not in output.out + output.err
