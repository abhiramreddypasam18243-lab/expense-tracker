"""Step 13 — web mode: real uploads on the hosted site, in memory only (AC-67 to AC-69)."""

import tempfile
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def web_env(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPENSES_DEMO", "1")
    monkeypatch.setenv("EXPENSES_DB", str(tmp_path / "real.db"))
    for name in ("EXPENSES_PUBLIC", "EXPENSES_PASSPHRASE_HASH", "EXPENSES_TLS_PROXY"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def new_session():
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def transactions(at):
    return at.session_state["_conn"].execute("SELECT COUNT(*) FROM transactions").fetchone()[0]


def banner_text(at):
    return " ".join(i.value for i in at.info)


def test_ac_67_session_database_is_in_memory(web_env):
    temp_dir = Path(tempfile.gettempdir())
    before = set(temp_dir.glob("expenses-*"))
    at = new_session()
    conn = at.session_state["_conn"]
    assert at.session_state["_db_path"] == ":memory:"
    assert conn.execute("PRAGMA database_list").fetchone()["file"] == ""  # SQLite: in-memory has no file
    assert transactions(at) > 0
    assert "sample data" in banner_text(at)
    assert not (web_env / "real.db").exists()
    assert set(temp_dir.glob("expenses-*")) == before


def test_ac_68_use_my_own_statements(web_env):
    at = new_session()
    at.button(key="use_own").click().run()
    assert not at.exception
    assert transactions(at) == 0
    assert at.session_state["_conn"].execute("SELECT COUNT(*) FROM categories").fetchone()[0] > 0
    assert "Private session" in banner_text(at) and "sample data" not in banner_text(at)

    at.switch_page("ui/import_data.py").run()
    assert not at.exception
    uploaders = at.get("file_uploader")
    assert len(uploaders) == 1 and uploaders[0].proto.multiple_files
    assert "in memory" in banner_text(at)


def test_ac_69_sessions_are_isolated(web_env):
    first = new_session()
    first.button(key="use_own").click().run()
    second = new_session()
    assert transactions(first) == 0
    assert transactions(second) > 0
    assert first.session_state["_conn"] is not second.session_state["_conn"]
