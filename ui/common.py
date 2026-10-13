"""Shared UI helpers: database handle, page header, month picker, empty state, formatting."""

from __future__ import annotations

import calendar
import html
import sqlite3
from decimal import Decimal

import streamlit as st

from expenses import insights

CONN_KEY = "_conn"
DB_PATH_KEY = "_db_path"


def get_conn() -> sqlite3.Connection:
    """The connection app.py opened for this script run."""
    return st.session_state[CONN_KEY]


# --- formatting (display only) ------------------------------------------------------------


def month_label(month: str, short: bool = False) -> str:
    """'2026-06' -> 'June 2026' (or 'Jun 26')."""
    year, mon = insights.parse_month(month)
    if short:
        return f"{calendar.month_abbr[mon]} {year % 100:02d}"
    return f"{calendar.month_name[mon]} {year}"


def compact_inr(minor: int) -> str:
    """Short Indian-style label for chart annotations: 18716314 -> '₹1.87L'."""
    rupees = abs(minor) // 100
    sign = "-" if minor < 0 else ""
    for size, suffix in ((10**7, "Cr"), (10**5, "L"), (10**3, "K")):
        if rupees >= size:
            value = (Decimal(rupees) / size).quantize(Decimal("0.01"))
            text = f"{value:f}".rstrip("0").rstrip(".")
            return f"{sign}₹{text}{suffix}"
    return f"{sign}₹{rupees}"


def esc(text: object) -> str:
    """Escape user data before putting it inside HTML."""
    return html.escape(str(text))


def nice_date(iso: str) -> str:
    """'2026-06-28' -> '28 Jun 2026'."""
    year, mon, day = iso.split("-")
    return f"{int(day)} {calendar.month_abbr[int(mon)]} {year}"


# --- layout pieces --------------------------------------------------------------------------------

_card_counter: dict[str, int] = {}


def card(name: str, highlight: bool = False, **kwargs):
    """A raised card (styled by ui/theme.py through its `st-key-card…` class). Use as a context manager."""
    prefix = "cardhl" if highlight else "card"
    return st.container(key=f"{prefix}-{name}", **kwargs)


def page_header(
    title: str, subtitle: str, months: list[str] | None = None, eyebrow_text: str | None = None
) -> str | None:
    """Eyebrow + gradient title + subtitle on the left; optional month picker on the right."""
    left, right = st.columns([3, 1], vertical_alignment="bottom")
    with left:
        if eyebrow_text:
            st.html(f'<div class="eyebrow-top">{esc(eyebrow_text)}</div>')
        st.title(title)
        st.html(f'<div class="page-sub">{esc(subtitle)}</div>')
    if not months:
        return None
    with right:
        current = st.session_state.get("month")
        index = months.index(current) if current in months else 0
        month = st.selectbox(
            "Month", months, index=index, format_func=month_label, label_visibility="collapsed"
        )
    st.session_state["month"] = month
    return month


def empty_state(title: str, body: str, link_page: str | None = None, link_label: str | None = None) -> None:
    from ui.components import empty_html  # local import: components imports nothing from here

    with card("empty"):
        st.html(empty_html(title, body))
        if link_page:
            _, mid, _ = st.columns([2, 1.2, 2])
            with mid:
                st.page_link(link_page, label=link_label or "Open", icon=":material/arrow_forward:")


def no_transactions_state() -> None:
    empty_state(
        "No transactions yet — import a statement",
        "Upload CSV exports from your banks and they will be cleaned, de-duplicated and categorised automatically.",
        link_page="ui/import_data.py",
        link_label="Import statements",
    )


def eyebrow(text: str, icon_name: str = "sparkle", sub: str | None = None) -> None:
    """Section title inside a card."""
    from ui.components import section_title

    st.html(section_title(text, icon_name, sub))
