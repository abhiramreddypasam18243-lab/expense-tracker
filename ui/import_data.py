"""Import: upload a CSV statement, see the report, browse and undo past imports."""

import pandas as pd
import streamlit as st

from expenses import auth, db
from expenses.importer import HEADER_SEARCH_ROWS, HeaderNotFoundError, import_csv, undo_import
from ui import demo
from ui.common import esc, eyebrow, get_conn, nice_date, page_header

conn = get_conn()
page_header(
    "Import", "Add a bank statement. Duplicates are skipped automatically, so overlapping files are safe."
)

accounts = db.list_accounts(conn)

is_demo = auth.load_config(tls_enabled=bool(st.get_option("server.sslCertFile"))).demo
submitted = False

# --- demo: synthetic statements only (spec 7.2) ---------------------------------------------------------
if is_demo:
    with st.container(border=True):
        eyebrow("New import")
        st.markdown(
            "Uploads are turned off in the demo. Add another **synthetic** statement instead — "
            "it uses a different random seed, so you'll see new rows (and how duplicates are handled)."
        )
        if st.button(
            "Add a synthetic statement", key="demo_import", type="primary", icon=":material/science:"
        ):
            seed = demo.DEMO_SEED + len(db.list_batches(conn))
            st.session_state["last_report"] = demo.import_synthetic(conn, seed, "Demo Credit Card")
            st.toast("Imported a synthetic statement", icon=":material/check_circle:")

# --- upload form -------------------------------------------------------------------------------------
else:
    with st.container(border=True):
        eyebrow("New import")
        with st.form("import_form", clear_on_submit=True, border=False):
            upload = st.file_uploader(
                "Statement (CSV)", type=["csv"], help="Processed in memory; never saved to disk."
            )
            account = st.text_input(
                "Account name",
                value=accounts[0] if accounts else "",
                placeholder="e.g. HDFC Savings",
                help="Used to keep accounts apart. The same row in two accounts is not a duplicate.",
            )
            submitted = st.form_submit_button("Import statement", type="primary", icon=":material/upload:")

if submitted:
    if upload is None:
        st.error("Choose a CSV file first.", icon=":material/error:")
    elif not account.strip():
        st.error("Enter an account name.", icon=":material/error:")
    else:
        try:
            text = upload.getvalue().decode("utf-8-sig")
            with st.spinner("Importing…"):
                report = import_csv(conn, text, account=account, source_name=upload.name)
            st.session_state["last_report"] = report
            st.toast(f"Imported {report.rows_imported} new transactions", icon=":material/check_circle:")
        except UnicodeDecodeError:
            st.error(
                "This file is not UTF-8 text. Export the statement as CSV (UTF-8) and try again.",
                icon=":material/error:",
            )
        except HeaderNotFoundError:
            st.error(
                f"Couldn't find a header row with date, description and amount columns in the first "
                f"{HEADER_SEARCH_ROWS} lines.",
                icon=":material/error:",
            )

report = st.session_state.get("last_report")
if report:
    with st.container(border=True):
        eyebrow(f"Last import · batch {report.batch_id}")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Rows in file", report.rows_total)
        m2.metric("Imported", report.rows_imported)
        m3.metric("Duplicates skipped", report.rows_duplicate)
        m4.metric("Rejected", report.rows_rejected)
        if report.errors:
            st.warning(f"{len(report.errors)} row(s) could not be imported:", icon=":material/warning:")
            st.dataframe(
                pd.DataFrame(report.errors, columns=["Line", "Reason"]), hide_index=True, width="stretch"
            )

# --- history ------------------------------------------------------------------------------------------
with st.container(border=True):
    eyebrow("Import history")
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

with st.expander("Which CSV formats work?", icon=":material/help:"):
    st.markdown(
        """
- One header row (it may come after a few lines of bank preamble) with a **date**, a **description**
  (narration / particulars) and either an **amount** column or **debit + credit** (withdrawal / deposit) columns.
- Dates are read day-first (`03/04/2026` is 3 April). Amounts may use `₹`, `Rs`, commas, `Dr`/`Cr` or `( )`.
- An optional **currency** column converts foreign rows to INR using that day's ECB rate.
- For demo data, run `python tools/make_synthetic.py --out data/synthetic.csv`.
"""
    )
