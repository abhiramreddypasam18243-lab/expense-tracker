"""Design tokens, chart colours and the global stylesheet (spec 7.2 "Visual design system").

One set of token names with light and dark values. The stylesheet writes each colour as CSS
`light-dark(light, dark)`, so it follows Streamlit's theme instantly — also when the viewer
switches it in the ⋮ menu, which doesn't rerun the script. Python-drawn colours (charts) use
current() and catch up on the next rerun.
"""

from __future__ import annotations

import re

import streamlit as st

LIGHT = {
    "--bg": "#F4F5FB",
    "--glow-1": "rgba(99, 102, 241, 0.16)",
    "--glow-2": "rgba(236, 72, 153, 0.10)",
    "--surface": "#FFFFFF",
    "--surface-2": "#F7F8FC",
    "--border": "rgba(15, 23, 42, 0.07)",
    "--text": "#0F172A",
    "--muted": "#64748B",
    "--accent": "#5B5BF6",
    "--accent-2": "#8B5CF6",
    "--accent-soft": "rgba(91, 91, 246, 0.10)",
    "--gradient": "linear-gradient(135deg, #6366F1 0%, #8B5CF6 55%, #EC4899 100%)",
    "--good": "#15803D",
    "--good-soft": "rgba(22, 163, 74, 0.12)",
    "--bad": "#DC2626",
    "--bad-soft": "rgba(220, 38, 38, 0.10)",
    "--warn": "#B45309",
    "--warn-soft": "rgba(245, 158, 11, 0.16)",
    "--track": "rgba(15, 23, 42, 0.07)",
    "--shadow": "0 1px 2px rgba(16, 24, 40, 0.04), 0 10px 28px -12px rgba(16, 24, 40, 0.14)",
    "--shadow-hover": "0 2px 4px rgba(16, 24, 40, 0.05), 0 18px 40px -14px rgba(79, 70, 229, 0.28)",
}
DARK = {
    "--bg": "#0A0E1A",
    "--glow-1": "rgba(99, 102, 241, 0.22)",
    "--glow-2": "rgba(236, 72, 153, 0.12)",
    "--surface": "#121829",
    "--surface-2": "#0F1424",
    "--border": "rgba(255, 255, 255, 0.07)",
    "--text": "#E7EAF3",
    "--muted": "#94A0B8",
    "--accent": "#8B8CFF",
    "--accent-2": "#B197FC",
    "--accent-soft": "rgba(139, 140, 255, 0.14)",
    "--gradient": "linear-gradient(135deg, #6366F1 0%, #8B5CF6 55%, #EC4899 100%)",
    "--good": "#4ADE80",
    "--good-soft": "rgba(74, 222, 128, 0.14)",
    "--bad": "#F87171",
    "--bad-soft": "rgba(248, 113, 113, 0.14)",
    "--warn": "#FBBF24",
    "--warn-soft": "rgba(251, 191, 36, 0.14)",
    "--track": "rgba(255, 255, 255, 0.08)",
    "--shadow": "0 1px 2px rgba(0, 0, 0, 0.3), 0 14px 34px -16px rgba(0, 0, 0, 0.7)",
    "--shadow-hover": "0 2px 6px rgba(0, 0, 0, 0.35), 0 20px 44px -16px rgba(99, 102, 241, 0.45)",
}

# Validated categorical palette (data-viz validator: passes lightness, chroma, colour-blind and
# normal-vision checks in both modes). Colour follows the category, never its rank.
CATEGORY_PALETTE = {
    "Food & Dining": ("#eb6834", "#d95926"),
    "Groceries": ("#1baf7a", "#199e70"),
    "Transport": ("#2a78d6", "#3987e5"),
    "Shopping": ("#e87ba4", "#d55181"),
    "Bills & Utilities": ("#eda100", "#c98500"),
    "Rent": ("#4a3aa7", "#9085e9"),
    "Entertainment": ("#e34948", "#e66767"),
    "Health": ("#008300", "#008300"),
}
OTHER_COLOR = {"light": "#A3A9B8", "dark": "#5B6478"}


def current() -> str:
    """'light' or 'dark' — the theme the viewer is actually seeing."""
    try:
        info = st.context.theme
        kind = info.get("type") if isinstance(info, dict) else getattr(info, "type", None)
    except Exception:  # noqa: BLE001 — older runtimes / tests: fall back to light
        kind = None
    return "dark" if kind == "dark" else "light"


def tokens(mode: str) -> dict[str, str]:
    return dict(DARK if mode == "dark" else LIGHT)


def category_color(name: str, mode: str) -> str:
    pair = CATEGORY_PALETTE.get(name)
    if pair is None:
        return OTHER_COLOR[mode]
    return pair[1] if mode == "dark" else pair[0]


_RULES = """
/* ---- page ---------------------------------------------------------------------------------- */
.stApp {
  background:
    radial-gradient(1100px 520px at 8% -8%, var(--glow-1), transparent 62%),
    radial-gradient(900px 480px at 100% -4%, var(--glow-2), transparent 60%),
    var(--bg) !important;
  background-attachment: fixed !important;
}
[data-testid="stHeader"] { background: transparent !important; }
[data-testid="stMainBlockContainer"] { max-width: 1240px; padding-top: 2.2rem; padding-bottom: 4rem; }
[data-testid="stAppDeployButton"] { display: none; }
.st-key-theme-sync, div:has(> .st-key-theme-sync) { display: none !important; }  /* hidden rerun button (ui/style.py) */
@keyframes rise { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }

/* ---- headings ------------------------------------------------------------------------------ */
[data-testid="stHeading"] h1 {
  font-weight: 800; letter-spacing: -0.035em; font-size: 2.45rem; line-height: 1.1; padding-bottom: 0.15rem;
  background: var(--gradient); -webkit-background-clip: text; background-clip: text; color: transparent;
}
.eyebrow-top { font-size: .74rem; font-weight: 700; letter-spacing: .12em; text-transform: uppercase;
  color: var(--accent); margin-bottom: -0.4rem; }
.page-sub { color: var(--muted); font-size: 1.02rem; margin-top: .35rem; }

/* ---- cards: every keyed "card-*" container --------------------------------------------------- */
[class*="st-key-card"] {
  background: var(--surface); border: 1px solid var(--border); border-radius: 18px;
  box-shadow: var(--shadow); padding: 1.15rem 1.3rem 1.2rem; animation: rise .45s ease both;
  transition: box-shadow .2s ease, transform .2s ease;
}
[class*="st-key-card"]:hover { box-shadow: var(--shadow-hover); }
[class*="st-key-cardhl"] {
  background: linear-gradient(var(--surface), var(--surface)) padding-box, var(--gradient) border-box;
  border: 1.5px solid transparent;
}
.section-title { display: flex; align-items: center; gap: .55rem; font-weight: 700; font-size: .98rem;
  color: var(--text); margin: 0 0 .15rem; }
.section-title .chip { width: 28px; height: 28px; border-radius: 9px; display: grid; place-items: center;
  background: var(--accent-soft); color: var(--accent); }
.section-sub { color: var(--muted); font-size: .84rem; margin: 0 0 .5rem; }

/* ---- story banner ---------------------------------------------------------------------------- */
.story { position: relative; overflow: hidden; border-radius: 20px; padding: 1.35rem 1.5rem;
  background: var(--gradient); color: #fff; box-shadow: 0 18px 40px -18px rgba(99, 102, 241, .65);
  animation: rise .45s ease both; }
.story::after { content: ""; position: absolute; right: -60px; top: -80px; width: 260px; height: 260px;
  border-radius: 50%; background: rgba(255, 255, 255, .12); }
.story .label { font-size: .74rem; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; opacity: .85; }
.story .text { font-size: 1.22rem; line-height: 1.45; margin-top: .3rem; max-width: 60rem; position: relative; z-index: 1; }
.story b { font-weight: 800; }

/* ---- KPI cards ------------------------------------------------------------------------------- */
.kpi-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 1rem; margin: .2rem 0 .3rem; }
@media (max-width: 1000px) { .kpi-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 560px) { .kpi-grid { grid-template-columns: 1fr; } }
.kpi { background: var(--surface); border: 1px solid var(--border); border-radius: 18px; padding: 1rem 1.1rem .7rem;
  box-shadow: var(--shadow); animation: rise .45s ease both; transition: box-shadow .2s, transform .2s; }
.kpi:hover { box-shadow: var(--shadow-hover); transform: translateY(-2px); }
.kpi .top { display: flex; align-items: center; justify-content: space-between; }
.kpi .label { color: var(--muted); font-size: .84rem; font-weight: 600; }
.kpi .icon { width: 34px; height: 34px; border-radius: 11px; display: grid; place-items: center; }
.kpi .value { font-family: "Plus Jakarta Sans", Inter, sans-serif; font-weight: 800; font-size: 1.62rem;
  letter-spacing: -0.03em; color: var(--text); margin: .45rem 0 .25rem; font-variant-numeric: tabular-nums; }
.kpi .foot { display: flex; align-items: flex-end; justify-content: space-between; gap: .5rem; min-height: 34px; }
.delta { display: inline-flex; align-items: center; gap: .2rem; font-size: .76rem; font-weight: 700;
  padding: .18rem .5rem; border-radius: 999px; white-space: nowrap; }
.delta.good { color: var(--good); background: var(--good-soft); }
.delta.bad { color: var(--bad); background: var(--bad-soft); }
.delta.flat { color: var(--muted); background: var(--track); }
.kpi .vs { color: var(--muted); font-size: .72rem; margin-top: .15rem; }

.legend-grid { display: grid; grid-template-columns: 1fr; padding: 0 .2rem; }
/* ---- legend, lists, budgets ------------------------------------------------------------------ */
.legend-row { display: flex; align-items: center; gap: .6rem; padding: .42rem 0; font-size: .9rem;
  border-bottom: 1px dashed var(--border); }
.legend-row:last-child { border-bottom: none; }
.legend-row .dot { width: .7rem; height: .7rem; border-radius: 4px; flex: none; }
.legend-row .name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text); }
.legend-row .amt { margin-left: auto; font-variant-numeric: tabular-nums; font-weight: 700; color: var(--text); }
.legend-row .pct { width: 2.7rem; text-align: right; color: var(--muted); font-variant-numeric: tabular-nums; }
.legend-note { color: var(--muted); font-size: .78rem; margin-top: .35rem; }
.list-row { display: flex; align-items: center; gap: .8rem; padding: .7rem .2rem; border-bottom: 1px solid var(--border); }
.list-row:last-child { border-bottom: none; }
.list-row .badge-ic { width: 38px; height: 38px; border-radius: 12px; flex: none; display: grid; place-items: center; }
.list-row .main { min-width: 0; flex: 1; }
.item-title { font-weight: 700; color: var(--text); font-variant-numeric: tabular-nums; }
.item-meta { color: var(--muted); font-size: .84rem; }
.list-row .side { text-align: right; flex: none; }
.pill { font-size: .74rem; font-weight: 700; padding: .2rem .6rem; border-radius: 999px; white-space: nowrap; }
.pill.ok, .pill.good { color: var(--good); background: var(--good-soft); }
.pill.warning { color: var(--warn); background: var(--warn-soft); }
.pill.over, .pill.bad { color: var(--bad); background: var(--bad-soft); }
.pill.accent { color: var(--accent); background: var(--accent-soft); }
.pill.muted { color: var(--muted); background: var(--track); }
.budget { padding: .55rem 0 .7rem; }
.budget-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: .45rem; }
.track { height: .6rem; border-radius: 999px; background: var(--track); overflow: hidden; }
.fill { height: 100%; border-radius: 999px; }
.fill.ok { background: linear-gradient(90deg, #22C55E, #16A34A); }
.fill.warning { background: linear-gradient(90deg, #FBBF24, #F59E0B); }
.fill.over { background: linear-gradient(90deg, #F87171, #DC2626); }
.budget .item-meta { margin-top: .35rem; }

/* ---- empty state ----------------------------------------------------------------------------- */
.empty { text-align: center; padding: 2.2rem 1rem 1rem; }
.empty h3 { margin: .9rem 0 .35rem; font-size: 1.3rem; color: var(--text); }
.empty p { color: var(--muted); margin: 0 auto; max-width: 32rem; }

/* ---- widgets --------------------------------------------------------------------------------- */
[data-testid="stBaseButton-primary"], [data-testid="stBaseButton-primaryFormSubmit"] {
  background: var(--gradient) !important; border: 0 !important; color: #fff !important; font-weight: 600;
  box-shadow: 0 8px 20px -8px rgba(99, 102, 241, .7); transition: transform .15s ease, box-shadow .15s ease;
}
[data-testid="stBaseButton-primary"]:hover, [data-testid="stBaseButton-primaryFormSubmit"]:hover {
  transform: translateY(-1px); box-shadow: 0 12px 26px -10px rgba(99, 102, 241, .8);
}
[data-testid="stBaseButton-secondary"] { background: var(--surface) !important; border: 1px solid var(--border) !important;
  box-shadow: 0 1px 2px rgba(16, 24, 40, .05); }
[data-baseweb="input"], [data-baseweb="select"] > div, [data-baseweb="textarea"] {
  border-radius: 12px !important; background: var(--surface-2) !important; border-color: var(--border) !important; }
[data-testid="stTabs"] [role="tablist"] { gap: .3rem; background: var(--surface-2); padding: .3rem; border-radius: 14px;
  border: 1px solid var(--border); width: fit-content; max-width: 100%; overflow-x: auto; }
[data-testid="stTab"] { border-radius: 10px !important; padding: .45rem .95rem !important; height: auto !important; }
[data-testid="stTab"][aria-selected="true"] { background: var(--surface); box-shadow: 0 1px 3px rgba(16, 24, 40, .12); }
[data-testid="stTab"][aria-selected="true"] p { color: var(--accent); font-weight: 700; }
[data-baseweb="tab-highlight"], [data-baseweb="tab-border"] { display: none !important; }
[data-testid="stAlertContainer"] { border-radius: 14px !important; border: 1px solid var(--border); }
[data-testid="stExpander"] details { border-radius: 14px !important; border: 1px solid var(--border) !important;
  background: var(--surface); }
[data-testid="stDataFrame"] { border-radius: 14px; overflow: hidden; border: 1px solid var(--border); }
[data-testid="stFileUploaderDropzone"] { border: 1.5px dashed var(--accent) !important; background: var(--accent-soft) !important;
  border-radius: 16px !important; }
[data-testid="stChatMessage"] { background: var(--surface); border: 1px solid var(--border); border-radius: 16px;
  box-shadow: var(--shadow); padding: .9rem 1rem; }
[data-testid="stChatInput"] { border-radius: 16px; box-shadow: var(--shadow); }
[data-testid="stPopoverButton"] { border-radius: 10px !important; }

/* ---- sidebar --------------------------------------------------------------------------------- */
[data-testid="stSidebar"] { background: var(--surface) !important; border-right: 1px solid var(--border); }
[data-testid="stSidebarNavLink"] { border-radius: 12px; padding: .5rem .75rem; margin: .1rem 0; transition: background .15s; }
[data-testid="stSidebarNavLink"]:hover { background: var(--accent-soft); }
[data-testid="stSidebarNavLink"][aria-current="page"] { background: var(--accent-soft); }
[data-testid="stSidebarNavLink"][aria-current="page"] span { color: var(--accent) !important; font-weight: 700; }
.side-note { margin-top: .6rem; padding: .85rem .95rem; border-radius: 14px; background: var(--accent-soft);
  color: var(--text); font-size: .82rem; line-height: 1.45; }
.side-note b { color: var(--accent); }

@media (max-width: 640px) {
  [data-testid="stMainBlockContainer"] { padding-top: 3.6rem; }  /* clear the phone header */
  [data-testid="stHeading"] h1 { font-size: 1.9rem; }
  .story .text { font-size: 1.05rem; }
  [class*="st-key-card"] { padding: .95rem 1rem; border-radius: 16px; }
}
"""


_COLOUR = re.compile(r"#[0-9A-Fa-f]{3,8}\b|rgba?\([^)]*\)")


def _adaptive(light: str, dark: str) -> str:
    """One CSS value for both modes: light-dark() around each colour that differs.

    Shadows keep the light geometry, with each of their colours adapting.
    """
    if light == dark:
        return light
    if _COLOUR.fullmatch(light) and _COLOUR.fullmatch(dark):
        return f"light-dark({light}, {dark})"
    if len(_COLOUR.findall(light)) != len(_COLOUR.findall(dark)):
        return light
    dark_colours = iter(_COLOUR.findall(dark))
    return _COLOUR.sub(lambda m: f"light-dark({m.group(0)}, {next(dark_colours)})", light)


def css(mode: str | None = None) -> str:
    """The global stylesheet. It adapts to both themes by itself; `mode` is accepted for old callers.

    No `color-scheme` is set here: Streamlit sets it on the app for the theme the viewer chose.
    """
    variables = "\n".join(f"  {name}: {_adaptive(LIGHT[name], DARK[name])};" for name in LIGHT)
    return f"<style>\n:root {{\n{variables}\n}}\n{_RULES}</style>"
