"""Shared UI helpers: database handle, page header, month picker, empty state, formatting."""

from __future__ import annotations

import calendar
import html
import sqlite3
from decimal import Decimal

import streamlit as st

from expenses import insights

CONN_KEY = "_conn"


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


def page_header(title: str, subtitle: str, months: list[str] | None = None) -> str | None:
    """Title + subtitle on the left; optional month picker on the right. Returns the chosen month."""
    left, right = st.columns([3, 1], vertical_alignment="bottom")
    with left:
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


def empty_state(
    title: str, body: str, icon: str = "🗂️", link_page: str | None = None, link_label: str | None = None
) -> None:
    with st.container(border=True):
        st.markdown(
            f'<div class="empty"><div class="icon">{icon}</div><h3>{esc(title)}</h3><p>{esc(body)}</p></div>',
            unsafe_allow_html=True,
        )
        if link_page:
            _, mid, _ = st.columns([2, 1, 2])
            with mid:
                st.page_link(link_page, label=link_label or "Open", icon=":material/arrow_forward:")


def no_transactions_state() -> None:
    empty_state(
        "No transactions yet — import a statement",
        "Upload a CSV export from your bank and it will be cleaned, de-duplicated and categorised automatically.",
        icon="📥",
        link_page="ui/import_data.py",
        link_label="Import a statement",
    )


def eyebrow(text: str) -> None:
    st.html(f'<div class="eyebrow">{esc(text)}</div>')
