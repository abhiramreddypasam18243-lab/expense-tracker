"""Import: one or more statements at once (combined preview, transfer matching), history and undo."""

import hashlib

import pandas as pd
import streamlit as st

from expenses import auth, combine, db
from expenses.columns import ROLE_NAMES
from expenses.importer import undo_import
from expenses.money import format_inr
from expenses.pdf_import import is_pdf
from ui import demo
from ui.common import card, esc, eyebrow, get_conn, nice_date, page_header

conn = get_conn()
page_header(
    "Import",
    "Add one or more bank statements at once. Duplicates are skipped and transfers between your accounts are matched.",
    eyebrow_text="Data",
)

accounts = db.list_accounts(conn)
is_demo = auth.load_config(tls_enabled=bool(st.get_option("server.sslCertFile"))).demo


IGNORE = "Ignore"
ROLE_BY_LABEL = {label: role for role, label in ROLE_NAMES.items()}


def chosen_roles(file_id: str) -> dict[str, int] | None:
    """The user's column roles for a headerless file (from its dropdowns), or None to keep the guess."""
    width = st.session_state.get(f"guess-width-{file_id}")
    if width is None:
        return None
    labels = [st.session_state.get(f"role-{file_id}-{c}") for c in range(width)]
    if None in labels:
        return None
    roles: dict[str, int] = {}
    for col, label in enumerate(labels):
        if label != IGNORE:
            roles.setdefault(ROLE_BY_LABEL[label], col)  # a role picked twice: the first column wins
    return roles


def show_column_guess(file_id: str, f: combine.FilePreview) -> None:
    """Columns guessed for a file without a heading row: sample rows + one role dropdown per column (I11)."""
    guess = f.inferred or st.session_state.get(f"guess-{file_id}")
    if guess is None:
        return
    st.session_state[f"guess-{file_id}"] = guess  # keep the dropdowns even while the choice is invalid
    st.session_state[f"guess-width-{file_id}"] = guess.width
    with card(f"guess-{file_id}"):
        eyebrow(f"Columns guessed · {f.source_name}", "bars", "This file has no heading row")
        if f.inferred is not None and f.inferred.balance_checked:
            st.success(
                "Checked against the running balance: spends and income have the right signs.",
                icon=":material/verified:",
            )
        else:
            st.warning(
                "Couldn't check these against a running balance. Look at the first rows below and fix any "
                "column that's wrong — especially which one is money **out** (Debit) and money **in** (Credit).",
                icon=":material/help:",
            )
        role_of = {col: role for role, col in guess.roles.items()}
        options = [IGNORE, *ROLE_NAMES.values()]
        picks = st.columns(guess.width)
        for col, slot in enumerate(picks):
            current = ROLE_NAMES[role_of[col]] if col in role_of else IGNORE
            slot.selectbox(
                f"Column {col + 1}",
                options,
                index=options.index(current),
                key=f"role-{file_id}-{col}",
            )
        labels = [st.session_state.get(f"role-{file_id}-{c}", IGNORE) for c in range(guess.width)]
        repeated = sorted({x for x in labels if x != IGNORE and labels.count(x) > 1})
        if repeated:
            st.caption(f"Used more than once (the first column counts): {', '.join(repeated)}")
        sample = (f.inferred or guess).sample
        st.dataframe(
            pd.DataFrame(
                [(row + [""] * guess.width)[: guess.width] for row in sample],
                columns=[f"Column {c + 1}" for c in range(guess.width)],
            ),
            hide_index=True,
            width="stretch",
        )


def show_combined_preview(plan: combine.CombinedPreview) -> set[int]:
    """Per-file table + transfer pairs with tick boxes. Returns the ticked pair indexes."""
    files_df = pd.DataFrame(
        {
            "File": [f.source_name for f in plan.files],
            "Account": [f.account or "—" for f in plan.files],
            "Rows": [f.rows_total for f in plan.files],
            "New": [f.new for f in plan.files],
            "Duplicates": [f.duplicate for f in plan.files],
            "Rejected": [f.rejected for f in plan.files],
            "Status": [f"⚠ {f.problem}" if f.problem else "✓ Ready" for f in plan.files],
        }
    )
    st.dataframe(files_df, hide_index=True, width="stretch")
    for f in plan.files:  # the table cell can be too narrow for the whole message
        if f.problem:
            st.warning(f"**{f.source_name}**: {f.problem}", icon=":material/warning:")
    errors = [(f.source_name, line, reason) for f in plan.files for line, reason in f.errors]
    if errors:
        with st.expander(f"{len(errors)} row(s) will be skipped", icon=":material/warning:"):
            st.dataframe(
                pd.DataFrame(errors, columns=["File", "Line", "Reason"]), hide_index=True, width="stretch"
            )

    if not plan.pairs:
        st.caption("No transfers between your accounts found in these files.")
        return set()
    st.markdown(
        f"**{len(plan.pairs)} transfer(s) between your accounts.** Ticked pairs are saved as *Transfers*, "
        "so they don't count as spending or income. Untick any that aren't really yours."
    )
    pairs_df = pd.DataFrame(
        {
            "Use": [True] * len(plan.pairs),
            "Out of": [f"{p.debit.account} · {nice_date(p.debit.txn_date.isoformat())}" for p in plan.pairs],
            "Into": [f"{p.credit.account} · {nice_date(p.credit.txn_date.isoformat())}" for p in plan.pairs],
            "Amount": [format_inr(p.credit.amount_minor) for p in plan.pairs],
            "Details": [f"{p.debit.description} → {p.credit.description}" for p in plan.pairs],
        }
    )
    edited = st.data_editor(
        pairs_df,
        key=f"pairs-{st.session_state.get('plan_sig')}",
        hide_index=True,
        width="stretch",
        disabled=["Out of", "Into", "Amount", "Details"],
        column_config={
            "Use": st.column_config.CheckboxColumn(width="small"),
            "Amount": st.column_config.TextColumn(alignment="right"),
        },
    )
    return {i for i, use in enumerate(edited["Use"]) if use}


# --- web mode, still on sample data (spec 7.2) ------------------------------------------------------------
if is_demo and demo.is_sample():
    with card("import-sample"):
        eyebrow("New import")
        st.markdown(
            "You're looking at **sample data**. To import your own statements, click **Use my own statements** "
            "at the top of the page — that clears the sample first.\n\n"
            "Or add another **synthetic** statement to the sample (a different random seed, so you'll see new "
            "rows and how duplicates are handled)."
        )
        if st.button(
            "Add a synthetic statement", key="demo_import", type="primary", icon=":material/science:"
        ):
            seed = demo.DEMO_SEED + len(db.list_batches(conn))
            st.session_state["last_report"] = demo.import_synthetic(conn, seed, "Demo Credit Card")
            st.toast("Imported a synthetic statement", icon=":material/check_circle:")

# --- combined upload (spec I9) --------------------------------------------------------------------------------
else:
    if is_demo:
        st.info(
            "Your statements are processed **in memory** for this visit only and are gone when you leave. "
            "Nothing is saved on the server. While in use, the files pass through the host's servers.",
            icon=":material/lock:",
        )
    with card("import-new"):
        eyebrow("New import")
        upload_version = st.session_state.setdefault("upload_version", 0)
        uploads = st.file_uploader(
            "Statements (CSV or PDF) — one per account, as many as you like",
            type=["csv", "pdf"],
            accept_multiple_files=True,
            key=f"uploads-{upload_version}",
            help="Processed in memory; never saved to disk.",
        )
        if uploads:
            st.markdown("**Which account is each file?** Pick one you've used before or type a new name.")
            inputs = []
            for up in uploads:
                data = up.getvalue()
                pdf = is_pdf(data)
                name_col, account_col = st.columns([2, 3], vertical_alignment="center")
                size = f"{up.size / 1024:.1f} KB" if up.size >= 1024 else f"{up.size} bytes"
                name_col.html(
                    f'<div class="item-title">{esc(up.name)}</div>'
                    f'<div class="item-meta">{"PDF · " if pdf else ""}{size}</div>'
                )
                account = account_col.selectbox(
                    f"Account for {up.name}",
                    options=accounts,
                    index=None,
                    placeholder="Pick an account or type a new one, e.g. HDFC Savings",
                    accept_new_options=True,
                    key=f"account-{up.file_id}",
                    label_visibility="collapsed",
                )
                password = None
                if pdf:  # used once, in memory; never stored or logged
                    password = (
                        account_col.text_input(
                            f"PDF password for {up.name}",
                            type="password",
                            key=f"pdf-password-{up.file_id}",
                            placeholder="PDF password, if it has one (often your date of birth or customer ID)",
                            label_visibility="collapsed",
                        )
                        or None
                    )
                inputs.append(
                    combine.StatementInput(
                        data, account or "", up.name, password=password, column_roles=chosen_roles(up.file_id)
                    )
                )

            # Re-parse only when the files or accounts change (not on every click).
            # (The password is part of the key as a hash only, so a new password re-runs the conversion.)
            signature = tuple(
                (
                    up.file_id,
                    i.account,
                    hashlib.sha256((i.password or "").encode()).hexdigest(),
                    tuple(sorted((i.column_roles or {}).items())),
                )
                for up, i in zip(uploads, inputs, strict=True)
            )
            if st.session_state.get("plan_sig") != signature:
                st.session_state["plan"] = combine.preview(conn, inputs)
                st.session_state["plan_sig"] = signature
            plan = st.session_state["plan"]

            st.divider()
            eyebrow("Preview — nothing is saved yet")
            confirmed = show_combined_preview(plan)
            for up, f in zip(uploads, plan.files, strict=True):
                show_column_guess(up.file_id, f)
            for f in plan.files:  # every converted PDF can be downloaded as CSV (I10)
                if f.converted_csv is not None:
                    st.download_button(
                        f"Download {f.source_name} as CSV",
                        f.converted_csv,
                        file_name=f.source_name.rsplit(".", 1)[0] + ".csv",
                        mime="text/csv",
                        icon=":material/download:",
                        key=f"pdf-csv-{f.source_name}",
                    )
            if not plan.ok:
                st.error(
                    "Fix or remove the files marked ⚠ to continue. Nothing is imported until every file is ready.",
                    icon=":material/block:",
                )
            label = f"Import {plan.new_rows} new transaction(s) from {len(plan.files)} file(s)"
            if st.button(
                label, type="primary", icon=":material/upload:", disabled=not plan.ok or plan.new_rows == 0
            ):
                report = combine.commit(conn, plan, confirmed)
                st.session_state["last_report"] = report
                st.session_state["upload_version"] = upload_version + 1  # clears the uploader
                for key in ("plan", "plan_sig"):
                    st.session_state.pop(key, None)
                st.toast(f"Imported {report.rows_imported} new transactions", icon=":material/check_circle:")
                st.rerun()

# --- last import ------------------------------------------------------------------------------------------------
report = st.session_state.get("last_report")
if isinstance(report, combine.CombinedReport):
    with card("import-last"):
        eyebrow(f"Last import · batch {report.batch_id}")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Files", len(report.files))
        m2.metric("Imported", report.rows_imported)
        m3.metric("Duplicates skipped", sum(f.duplicate for f in report.files))
        m4.metric("Transfers matched", report.transfers_marked)
        st.dataframe(
            pd.DataFrame(
                [(f.source_name, f.account, f.imported, f.duplicate, f.rejected) for f in report.files],
                columns=["File", "Account", "Imported", "Duplicates", "Rejected"],
            ),
            hide_index=True,
            width="stretch",
        )
elif report:  # single synthetic import (demo mode)
    with card("import-last-demo"):
        eyebrow(f"Last import · batch {report.batch_id}")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Rows in file", report.rows_total)
        m2.metric("Imported", report.rows_imported)
        m3.metric("Duplicates skipped", report.rows_duplicate)
        m4.metric("Rejected", report.rows_rejected)

# --- history ------------------------------------------------------------------------------------------
with card("import-history"):
    eyebrow("Import history", "history")
    batches = db.list_batches(conn)
    if not batches:
        st.caption("No imports yet.")
    for b in batches:
        info, action = st.columns([5, 1], vertical_alignment="center")
        info.html(
            f'<div class="item-title">{esc(b["source_name"])}</div>'
            f'<div class="item-meta">{esc(b["account"])} · {nice_date(b["imported_at"][:10])} · '
            f"{b['rows_imported']} imported · {b['rows_duplicate']} duplicate · {b['rows_rejected']} rejected</div>"
        )
        with action.popover("Undo", icon=":material/undo:", width="stretch"):
            st.markdown(f"Remove the **{b['rows_imported']}** transactions this import added?")
            if st.button("Yes, undo import", key=f"undo-{b['id']}", type="primary"):
                removed = undo_import(conn, b["id"])
                st.session_state.pop("last_report", None)
                st.toast(f"Removed {removed} transactions", icon=":material/undo:")
                st.rerun()

with st.expander("Which files work?", icon=":material/help:"):
    st.markdown(
        """
- One header row (it may come after a few lines of bank preamble) with a **date**, a **description**
  (narration / particulars) and either an **amount** column or **debit + credit** (withdrawal / deposit) columns.
- Dates are read day-first (`03/04/2026` is 3 April). Amounts may use `₹`, `Rs`, commas, `Dr`/`Cr` or `( )`.
- An optional **currency** column converts foreign rows to INR using that day's ECB rate.
- **PDF statements** from net banking work too, including password-protected ones. Scanned PDFs (photos of
  paper) can't be read — download the statement again, or export it as CSV.
- Different banks in one go are fine: each file's columns are detected on their own.
- For demo data, run `python tools/make_synthetic.py --out data/synthetic.csv`.
"""
    )
