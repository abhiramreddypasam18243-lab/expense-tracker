"""Streamlit entry point: page config, theme CSS, database handle and navigation only."""

import streamlit as st

from expenses import __version__, categorize, db
from ui import style
from ui.common import CONN_KEY

st.set_page_config(page_title="Expense Tracker", page_icon=":material/account_balance_wallet:", layout="wide")
style.inject()

pages = [
    st.Page("ui/dashboard.py", title="Dashboard", icon=":material/space_dashboard:", default=True),
    st.Page(
        "ui/transactions.py", title="Transactions", url_path="transactions", icon=":material/receipt_long:"
    ),
    st.Page("ui/import_data.py", title="Import", url_path="import", icon=":material/upload_file:"),
    st.Page("ui/insights_page.py", title="Insights", url_path="insights", icon=":material/insights:"),
    st.Page("ui/settings.py", title="Settings", url_path="settings", icon=":material/tune:"),
]

st.logo("ui/assets/logo.svg", size="large", icon_image="ui/assets/icon.svg")
with st.sidebar:
    st.caption(f"Private · your data stays on this machine · v{__version__}")

page = st.navigation(pages)

# One connection per script run (SQLite is cheap to open); closed even if the page reruns or fails.
conn = db.connect()
categorize.seed_defaults(conn)
st.session_state[CONN_KEY] = conn
try:
    page.run()
finally:
    conn.close()
