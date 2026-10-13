"""Step 7 — Streamlit UI (AC-35 to AC-37)."""

import re
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from expenses import db
from expenses.importer import import_csv
from tools.make_synthetic import generate_csv

ROOT = Path(__file__).resolve().parent.parent
APP = str(ROOT / "app.py")
PAGES = [
    "ui/dashboard.py",
    "ui/transactions.py",
    "ui/import_data.py",
    "ui/insights_page.py",
    "ui/settings.py",
]


def no_network(currency, on):
    raise AssertionError("tests must never touch the network")


@pytest.fixture
def empty_db(tmp_path, monkeypatch):
    path = tmp_path / "ui.db"
    monkeypatch.setenv("EXPENSES_DB", str(path))
    return path


@pytest.fixture
def seeded_db(empty_db):
    conn = db.connect(empty_db)
    text = generate_csv(42, "amount")
    import_csv(conn, text, account="Demo Bank", source_name="demo.csv", fetch_rate=no_network)
    conn.close()
    return empty_db


def render(page):
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    if page != PAGES[0]:
        at.switch_page(page).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def all_text(at):
    parts = [m.value for m in at.markdown] + [c.value for c in at.caption] + [h.value for h in at.header]
    parts += [t.value for t in at.title] + [s.value for s in at.subheader] + [i.value for i in at.info]
    parts += [e.proto.body for e in at.get("html")]  # Step 16: styled HTML components
    return "\n".join(parts)


@pytest.mark.parametrize("page", PAGES)
def test_ac_35_pages_render_empty(empty_db, page):
    render(page)


@pytest.mark.parametrize("page", PAGES)
def test_ac_35_pages_render_seeded(seeded_db, page):
    at = render(page)
    if page == "ui/dashboard.py":
        # Step 16: the KPI cards are styled HTML (class "kpi") instead of st.metric.
        html = "\n".join(e.proto.body for e in at.get("html"))
        assert all(f">{label}<" in html for label in ("Spend", "Income", "Net", "Transactions"))
        assert html.count('class="kpi"') == 4 and "₹" in html


def test_ac_36_layers_are_separated():
    for path in (ROOT / "expenses").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"^\s*(import|from)\s+streamlit", text, re.MULTILINE), path.name
    ui_files = [ROOT / "app.py", *(ROOT / "ui").glob("*.py")]
    assert len(ui_files) >= 7
    for path in ui_files:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\b(SELECT|INSERT|UPDATE|DELETE)\b", text), path.name
        assert "sqlite3.connect" not in text and ".execute(" not in text, path.name


@pytest.mark.parametrize("page", ["ui/dashboard.py", "ui/transactions.py"])
def test_ac_37_empty_state(empty_db, page):
    at = render(page)
    assert "No transactions yet" in all_text(at)
