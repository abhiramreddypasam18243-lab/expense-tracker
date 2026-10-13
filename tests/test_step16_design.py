"""Step 16 — visual redesign (AC-83 to AC-87)."""

import re
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from expenses import db
from expenses.importer import import_csv
from expenses.insights import CategoryTotal
from tools.make_synthetic import generate_csv
from ui import charts, theme

ROOT = Path(__file__).resolve().parent.parent
PAGES = [
    "ui/dashboard.py",
    "ui/transactions.py",
    "ui/import_data.py",
    "ui/insights_page.py",
    "ui/ca.py",
    "ui/settings.py",
]
# The SVG namespace (www.w3.org/2000/svg) is an identifier inside image data, never fetched.
EXTERNAL_URL = re.compile(r"https?://(?!localhost|127\.0\.0\.1|ollama\b|www\.w3\.org/2000/svg)")


# --- AC-83 fonts bundled, nothing external ----------------------------------------------------------


def test_ac_83_fonts_bundled_and_no_external_urls():
    fonts = ROOT / "static" / "fonts"
    for name in ("inter-latin-wght-normal.woff2", "plus-jakarta-sans-latin-wght-normal.woff2"):
        assert (fonts / name).read_bytes()[:4] == b"wOF2", name
    assert (fonts / "OFL-Inter.txt").exists() and (fonts / "OFL-PlusJakartaSans.txt").exists()
    config = (ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    assert re.search(r"enableStaticServing\s*=\s*true", config)
    assert "app/static/fonts/inter-latin-wght-normal.woff2" in config
    assert "app/static/fonts/plus-jakarta-sans-latin-wght-normal.woff2" in config
    for path in [ROOT / "app.py", ROOT / ".streamlit" / "config.toml", *(ROOT / "ui").glob("*.py")]:
        assert not EXTERNAL_URL.search(path.read_text(encoding="utf-8")), path.name


# --- AC-84 tokens and palette ---------------------------------------------------------------------------


def test_ac_84_tokens_and_palette():
    light, dark = theme.tokens("light"), theme.tokens("dark")
    assert set(light) == set(dark) and len(light) >= 12
    assert light["--bg"] != dark["--bg"] and light["--text"] != dark["--text"]
    for mode in ("light", "dark"):
        css = theme.css(mode)
        assert all(name in css for name in light)
    assert len(theme.CATEGORY_PALETTE) == 8
    assert theme.category_color("Food & Dining", "light") == "#eb6834"
    assert theme.category_color("Food & Dining", "dark") == "#d95926"
    assert theme.category_color("Education", "light") == theme.OTHER_COLOR["light"]


def test_ac_85_donut_groups_other():
    totals = [
        CategoryTotal("Shopping", "#000", 500),
        CategoryTotal("Education", "#000", 300),
        CategoryTotal("Food & Dining", "#000", 200),
        CategoryTotal("Uncategorised", "#000", 100),
    ]
    rows = charts.donut_rows(totals, "light")
    expected = [("Shopping", 500), ("Food & Dining", 200), ("Other", 400)]
    assert [(r.name, r.spend_minor) for r in rows] == expected
    assert rows[-1].parts == ["Education", "Uncategorised"]
    assert rows[0].color == theme.category_color("Shopping", "light")


# --- AC-85 / AC-86 pages ----------------------------------------------------------------------------------


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    def make(mode: str, colour: str = "light"):
        path = tmp_path / f"{mode}-{colour}.db"
        monkeypatch.setenv("EXPENSES_DB", str(path))
        monkeypatch.setenv("EXPENSES_DEMO", "1" if mode == "web" else "0")
        for name in ("EXPENSES_PUBLIC", "EXPENSES_PASSPHRASE_HASH", "EXPENSES_TLS_PROXY", "EXPENSES_AI_URL"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(theme, "current", lambda: colour)
        if mode == "seeded":
            c = db.connect(path)
            import_csv(c, generate_csv(42, "amount"), "Demo", "s.csv", fetch_rate=None)
            c.close()

    return make


def html_of(at) -> str:
    return "\n".join(e.proto.body for e in at.get("html"))


def run(page: str):
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    if page != PAGES[0]:
        at.switch_page(page).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_ac_85_dashboard_story_and_kpis(app_env):
    app_env("seeded")
    html = html_of(run(PAGES[0]))
    for label in ("Spend", "Income", "Net", "Transactions"):
        assert f">{label}<" in html, label
    assert "₹" in html and html.count("data:image/svg+xml") >= 8  # 4 icons + 4 sparklines (as images)
    assert re.search(r"you spent", html, re.I)


def test_ac_85_empty_state(app_env):
    app_env("empty")
    at = run(PAGES[0])
    assert "No transactions yet" in html_of(at) + "\n".join(m.value for m in at.markdown)


@pytest.mark.parametrize("colour", ["light", "dark"])
@pytest.mark.parametrize("mode", ["empty", "seeded", "web"])
def test_ac_86_every_page_renders(app_env, mode, colour):
    app_env(mode, colour)
    for page in PAGES:
        at = run(page)
        assert theme.tokens(colour)["--bg"] in html_of(at)  # the right token set was injected
