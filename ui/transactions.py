"""Transactions: filter, search, fix categories (manual override), export, add entries by hand."""

from datetime import date

import pandas as pd
import streamlit as st

from expenses import categorize, db, exporter, insights
from expenses.money import format_inr
from ui import manual_entry
from ui.common import card, get_conn, month_label, no_transactions_state, page_header

ALL = "All"
SOURCE_LABEL = {"rule": "Rule", "manual": "You", "default": "—", "model": "Model"}


def set_by(row) -> str:
    """'Model · 87%' for a learned suggestion (spec 5.1), else Rule / You / —."""
    if row["category_source"] == "model" and row["category_confidence"] is not None:
        confidence = row["category_confidence"]
        return "Model · 99%+" if confidence >= 0.995 else f"Model · {round(confidence * 100)}%"
    return SOURCE_LABEL[row["category_source"]]


conn = get_conn()
page_header(
    "Transactions",
    "Search, filter and fix categories. Your changes are never overwritten by rules.",
    eyebrow_text="Ledger",
)
manual_entry.show_toast()
add_col, _ = st.columns([1, 3])
if add_col.button(
    "Add transaction", type="primary", icon=":material/add:", key="manual-add", width="stretch"
):
    manual_entry.open_dialog()
manual_entry.render(conn)

months = insights.available_months(conn)
if not months:
    no_transactions_state()
    st.stop()

categories = categorize.list_categories(conn)
category_ids = {c["name"]: c["id"] for c in categories}

# --- filters -------------------------------------------------------------------------------------
filter_card = card("filters")
f1, f2, f3, f4 = filter_card.columns([1.2, 1.2, 1.2, 2])
default_month = st.session_state.get("month")
month = f1.selectbox(
    "Month",
    [ALL, *months],
    index=(months.index(default_month) + 1) if default_month in months else 0,
    format_func=lambda m: "All months" if m == ALL else month_label(m),
)
category = f2.selectbox(
    "Category", [ALL, *category_ids], format_func=lambda c: "All categories" if c == ALL else c
)
accounts = db.list_accounts(conn)
account = f3.selectbox("Account", [ALL, *accounts], format_func=lambda a: "All accounts" if a == ALL else a)
search = f4.text_input("Search", placeholder="Search descriptions, e.g. swiggy", icon=":material/search:")
if month != ALL:
    st.session_state["month"] = month

filters = {
    "category": None if category == ALL else category,
    "account": None if account == ALL else account,
    "search": search.strip() or None,
}
start, end = insights.month_range(month) if month != ALL else (None, None)
rows = db.list_transactions(conn, start=start, end=end, **filters)

if not rows:
    st.info("No transactions match these filters.", icon=":material/filter_alt_off:")
    st.stop()

spend = -sum(r["amount_minor"] for r in rows if r["amount_minor"] < 0)
income = sum(r["amount_minor"] for r in rows if r["amount_minor"] > 0)
table_card = card("table")
info_col, export_col = table_card.columns([4, 1], vertical_alignment="center")
info_col.caption(
    f"**{len(rows)}** transactions · spent **{format_inr(spend)}** · received **{format_inr(income)}**"
)
export_col.download_button(
    "Export CSV",
    exporter.export_csv(conn, month=None if month == ALL else month, **filters),
    file_name=f"transactions-{'all' if month == ALL else month}.csv",
    mime="text/csv",
    icon=":material/download:",
    width="stretch",
)

# --- editable table (only Category can change) ------------------------------------------------------
df = pd.DataFrame(
    {
        "id": [r["id"] for r in rows],
        "Date": [date.fromisoformat(r["txn_date"]) for r in rows],
        "Description": [r["description"] for r in rows],
        "Merchant": [r["merchant"] or "" for r in rows],
        "Category": [r["category_name"] for r in rows],
        "Set by": [set_by(r) for r in rows],
        "Amount": [format_inr(r["amount_minor"]) for r in rows],
        "Account": [r["account"] for r in rows],
    }
)

# A new key after every save (and per filter) so old edits can never apply to different rows.
version = st.session_state.setdefault("txn_editor_version", 0)
editor_key = f"txn-{month}-{category}-{account}-{search}-{version}"
edited = table_card.data_editor(
    df,
    key=editor_key,
    hide_index=True,
    width="stretch",
    height=min(640, 38 + 35 * len(df)),
    column_order=["Date", "Description", "Merchant", "Category", "Set by", "Amount", "Account"],
    disabled=["id", "Date", "Description", "Merchant", "Set by", "Amount", "Account"],
    column_config={
        "Date": st.column_config.DateColumn(format="D MMM YYYY", width=105),
        "Description": st.column_config.TextColumn(width=250),
        "Merchant": st.column_config.TextColumn(width=120),
        "Category": st.column_config.SelectboxColumn(options=list(category_ids), required=True, width=150),
        "Set by": st.column_config.TextColumn(
            width=100,
            help="Rule = a keyword rule · Model = smart categories (with its confidence) · You = your choice",
        ),
        "Amount": st.column_config.TextColumn(width=115, alignment="right"),
        "Account": st.column_config.TextColumn(width=110),
    },
)

changed = edited[edited["Category"] != df["Category"]]
if not changed.empty:
    for txn_id, new_category in zip(changed["id"], changed["Category"], strict=True):
        categorize.set_category(conn, int(txn_id), category_ids[new_category])
    st.session_state["txn_editor_version"] = version + 1
    st.toast(f"Updated {len(changed)} categor{'y' if len(changed) == 1 else 'ies'}", icon=":material/check:")
    st.rerun()

table_card.caption(
    "Tip: click a Category cell to change it — smart categories learn from your changes. "
    "Use Settings → Rules to fix many rows at once."
)

# --- entries added by hand: edit or delete (imported rows can't be changed, only re-categorised) -------
added = [r for r in rows if r["is_manual"]]
if added:
    with card("manual-entries"):
        st.markdown(f"**Added by hand** · {len(added)} in this view")
        pick_col, edit_col = st.columns([4, 1], vertical_alignment="bottom")
        by_id = {r["id"]: r for r in added}
        picked = pick_col.selectbox(
            "Entry",
            list(by_id),
            format_func=lambda i: (
                f"{date.fromisoformat(by_id[i]['txn_date']):%d %b %Y} · {by_id[i]['description']} · "
                f"{format_inr(by_id[i]['amount_minor'])} · {by_id[i]['account']}"
            ),
            key="manual-pick",
        )
        if edit_col.button("Edit", icon=":material/edit:", key="manual-edit", width="stretch"):
            manual_entry.open_dialog(picked)
            st.rerun()
