"""Step 8 — passphrase gate, demo mode, network isolation (AC-41 to AC-43)."""

import re
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from expenses import auth

ROOT = Path(__file__).resolve().parent.parent
APP = str(ROOT / "app.py")
NETWORK_IMPORT = re.compile(
    r"^\s*(import|from)\s+(requests|httpx|aiohttp|urllib\.request|urllib3|http\.client|socket)\b",
    re.MULTILINE,
)


@pytest.fixture
def clean_env(tmp_path, monkeypatch):
    for name in ("EXPENSES_PUBLIC", "EXPENSES_PASSPHRASE_HASH", "EXPENSES_DEMO", "EXPENSES_TLS_PROXY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("EXPENSES_DB", str(tmp_path / "gate.db"))
    return monkeypatch


def run_app():
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    return at


# --- AC-41 passphrase gate ----------------------------------------------------------------


def test_ac_41_hash_and_verify():
    encoded = auth.hash_passphrase("correct horse", iterations=1000)
    assert encoded.startswith("pbkdf2_sha256:1000:") and "$" not in encoded
    assert "correct horse" not in encoded
    assert auth.verify_passphrase("correct horse", encoded)
    assert not auth.verify_passphrase("wrong horse", encoded)
    assert not auth.verify_passphrase("correct horse", "garbage")
    assert auth.hash_passphrase("x", iterations=1000) != auth.hash_passphrase("x", iterations=1000)  # salted


def test_ac_41_config_rules():
    assert auth.load_config({}).public is False
    with pytest.raises(auth.ConfigError):
        auth.load_config({"EXPENSES_PUBLIC": "1"})
    with pytest.raises(auth.ConfigError):
        auth.load_config({"EXPENSES_PUBLIC": "1", "EXPENSES_PASSPHRASE_HASH": "not-a-hash"})
    good = auth.hash_passphrase("pw", iterations=1000)
    config = auth.load_config({"EXPENSES_PUBLIC": "1", "EXPENSES_PASSPHRASE_HASH": good}, tls_enabled=True)
    assert config.public and config.passphrase_hash == good


def test_ac_41_public_without_hash_refuses(clean_env):
    clean_env.setenv("EXPENSES_PUBLIC", "1")
    at = run_app()
    assert not at.exception
    assert any("EXPENSES_PASSPHRASE_HASH" in e.value for e in at.error)
    assert not [t for t in at.title if t.value == "Dashboard"]


def test_ac_41_wrong_then_right_passphrase(clean_env):
    clean_env.setenv("EXPENSES_PUBLIC", "1")
    clean_env.setenv("EXPENSES_PASSPHRASE_HASH", auth.hash_passphrase("open sesame", iterations=1000))
    clean_env.setenv("EXPENSES_TLS_PROXY", "1")  # AC-51: public mode needs HTTPS
    at = run_app()
    assert not [t for t in at.title if t.value == "Dashboard"]

    at.text_input(key="passphrase").input("nope")
    at.button(key="unlock").click().run()
    assert any("Wrong passphrase" in e.value for e in at.error)
    assert not [t for t in at.title if t.value == "Dashboard"]

    at.text_input(key="passphrase").input("open sesame")
    at.button(key="unlock").click().run()
    assert not at.exception
    assert [t for t in at.title if t.value == "Dashboard"]


# --- AC-42 demo mode ------------------------------------------------------------------------


def test_ac_42_demo_mode(clean_env, tmp_path):
    clean_env.setenv("EXPENSES_DEMO", "1")
    at = run_app()
    assert not at.exception
    assert any("sample data" in i.value for i in at.info)
    db_path = Path(at.session_state["_db_path"])
    assert db_path != tmp_path / "gate.db"
    assert db_path.resolve() != (ROOT / "data" / "expenses.db").resolve()
    kpis = "\n".join(e.proto.body for e in at.get("html"))  # Step 16: KPI cards are styled HTML
    assert ">Spend<" in kpis and 'class="kpi"' in kpis, "demo database should be seeded"
    assert not (tmp_path / "gate.db").exists()


def test_ac_42_demo_import_has_no_uploader(clean_env):
    clean_env.setenv("EXPENSES_DEMO", "1")
    at = run_app()
    at.switch_page("ui/import_data.py").run()
    assert not at.exception
    assert len(at.get("file_uploader")) == 0
    at.button(key="demo_import").click().run()
    assert not at.exception
    assert at.session_state["last_report"].rows_imported > 0


# --- AC-43 network isolation ---------------------------------------------------------------------


def test_ac_43_only_fx_uses_network():
    files = [
        ROOT / "app.py",
        *(ROOT / "expenses").glob("*.py"),
        *(ROOT / "ui").glob("*.py"),
        *(ROOT / "tools").glob("*.py"),
    ]
    offenders = [p.name for p in files if NETWORK_IMPORT.search(p.read_text(encoding="utf-8"))]
    assert sorted(offenders) == ["ai.py", "fx.py"]  # AC-43 amended in Step 15: ai.py talks to local Ollama
    fx_source = (ROOT / "expenses" / "fx.py").read_text(encoding="utf-8")
    hosts = set(re.findall(r"https?://([^/\"'\s]+)", fx_source))
    assert hosts == {"api.frankfurter.dev"}
