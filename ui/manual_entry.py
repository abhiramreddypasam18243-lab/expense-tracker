"""The "Add transaction" / "Edit entry" dialog for transactions not in any statement (spec I13)."""

from datetime import date

import streamlit as st

from expenses import categorize, db, manual
from expenses.money import MoneyParseError, format_inr, parse_amount, to_plain

AUTO = "Automatic (rules / smart categories)"
OPEN = "manual_dialog"  # "new" or the id being edited; kept until saved or dismissed


def open_dialog(target: str | int = "new") -> None:
    """Open the dialog. It stays open across full-page reruns (e.g. the theme-sync rerun)."""
    st.session_state[OPEN] = target


def _close() -> None:
    target = st.session_state.pop(OPEN, None)
    prefix = f"me-{target}-"
    for name in [k for k in st.session_state if str(k).startswith(prefix)]:
        del st.session_state[name]  # the next "Add transaction" starts empty


def _saved(message: str) -> None:
    _close()
    st.session_state["txn_editor_version"] = st.session_state.get("txn_editor_version", 0) + 1
    st.session_state["manual_toast"] = message
    st.rerun()


def render(conn) -> None:
    """Show the dialog if it is open. Call once per page run."""
    target = st.session_state.get(OPEN)
    if target is None:
        return
    entry = None
    if target != "new":
        entry = db.get_transaction(conn, int(target))
        if entry is None or not entry["is_manual"]:
            _close()
            return
    entry_dialog(conn, entry)


@st.dialog("Transaction", width="medium", on_dismiss=_close)
def entry_dialog(conn, entry=None) -> None:
    """entry: a transaction row to edit (added by hand), or None for a new one."""
    key = f"me-{entry['id'] if entry else 'new'}"
    spent_before = entry is None or entry["amount_minor"] < 0
    st.markdown(
        "**Edit entry**" if entry else "**Add a transaction** — cash, a card, a wallet, or anything else"
    )
    kind = st.segmented_control(
        "Type", ["Spent", "Received"], default="Spent" if spent_before else "Received", key=f"{key}-kind"
    )
    spent = kind != "Received"
    wanted = "expense" if spent else "income"
    categories = [c for c in categorize.list_categories(conn) if c["kind"] == wanted]
    names = [AUTO, *(c["name"] for c in categories)]
    current = entry["category_name"] if entry and entry["category_source"] == "manual" else AUTO
    accounts = manual.account_choices(conn)
    if entry and entry["account"] not in accounts:
        accounts.append(entry["account"])

    # A form: every field is sent together with the Save click, so nothing typed can be lost.
    with st.form(f"{key}-form", border=False, enter_to_submit=False):
        c1, c2 = st.columns(2)
        when = c1.date_input(
            "Date",
            value=date.fromisoformat(entry["txn_date"]) if entry else date.today(),
            max_value=date.today(),
            format="DD/MM/YYYY",
            key=f"{key}-date",
        )
        amount_text = c2.text_input(
            "Amount (₹)",
            value=to_plain(abs(entry["amount_minor"])) if entry else "",
            placeholder="e.g. 250 or 1,200.50",
            key=f"{key}-amount",
        )
        description = st.text_input(
            "Description",
            value=entry["description"] if entry else "",
            placeholder="e.g. Tea at the canteen" if spent else "e.g. Birthday gift from Amma",
            max_chars=manual.MAX_DESCRIPTION,
            key=f"{key}-desc",
        )
        c3, c4 = st.columns(2)
        category = c3.selectbox(
            "Category",
            names,
            index=names.index(current) if current in names else 0,
            key=f"{key}-cat-{wanted}",
            help="Automatic uses your rules first, then smart categories.",
        )
        account = c4.selectbox(
            "Paid with" if spent else "Received in",
            accounts,
            index=accounts.index(entry["account"]) if entry else 0,
            accept_new_options=True,
            key=f"{key}-acct",
            help="Pick one, or type a new name, e.g. Paytm wallet.",
        )
        if spent:
            st.caption(
                "Cash spending counts here; the ATM withdrawal itself is a move from your bank to cash."
            )
        submitted = st.form_submit_button("Save", type="primary", icon=":material/check:", key=f"{key}-save")

    if submitted:
        try:
            amount = parse_amount(amount_text)
        except (MoneyParseError, TypeError):
            st.error("Enter the amount as a number, e.g. 250 or 1,200.50.", icon=":material/error:")
            return
        if amount <= 0:
            st.error(
                "Enter the amount without a minus sign; choose Spent or Received above.",
                icon=":material/error:",
            )
            return
        signed = -amount if spent else amount
        category_id = None if category == AUTO else categorize.category_id(conn, category)
        try:
            if entry:
                manual.update(conn, entry["id"], when, signed, description, account or "", category_id)
            else:
                manual.add(conn, when, signed, description, account or "", category_id)
        except manual.EntryError as exc:
            st.error(str(exc), icon=":material/error:")
            return
        _saved(f"{'Updated' if entry else 'Added'}: {format_inr(signed)}")
    if entry:
        with st.popover("Delete this entry", icon=":material/delete:"):
            st.write("Delete this entry? This can't be undone.")
            if st.button("Yes, delete", type="primary", key=f"{key}-delete"):
                manual.delete(conn, entry["id"])
                _saved("Entry deleted")


def show_toast() -> None:
    """Confirmation after the dialog closed (the dialog itself reruns the page)."""
    message = st.session_state.pop("manual_toast", None)
    if message:
        st.toast(message, icon=":material/check:")
