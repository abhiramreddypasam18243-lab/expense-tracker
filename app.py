"""Streamlit entry point: config checks, passphrase gate, database handle and navigation only."""

import streamlit as st

from expenses import __version__, auth, categorize, db
from ui import demo, gate, style
from ui.common import CONN_KEY, DB_PATH_KEY

st.set_page_config(page_title="Expense Tracker", page_icon=":material/account_balance_wallet:", layout="wide")
style.inject()
st.logo("ui/assets/logo.svg", size="large", icon_image="ui/assets/icon.svg")

# 1. Refuse to serve anything if the configuration is unsafe (spec 8.1).
try:
    config = auth.load_config()
except auth.ConfigError as exc:
    st.error(str(exc), icon=":material/gpp_bad:")
    st.stop()

# 2. Passphrase gate (only when a hash is configured).
gate.require_passphrase(config)

pages = [
    st.Page("ui/dashboard.py", title="Dashboard", icon=":material/space_dashboard:", default=True),
    st.Page(
        "ui/transactions.py", title="Transactions", url_path="transactions", icon=":material/receipt_long:"
    ),
    st.Page("ui/import_data.py", title="Import", url_path="import", icon=":material/upload_file:"),
    st.Page("ui/insights_page.py", title="Insights", url_path="insights", icon=":material/insights:"),
    st.Page("ui/settings.py", title="Settings", url_path="settings", icon=":material/tune:"),
]

with st.sidebar:
    if config.demo:
        st.caption(f"Demo with synthetic data · v{__version__}")
    else:
        st.caption(f"Private · your data stays on this machine · v{__version__}")

page = st.navigation(pages)

# 3. Demo mode uses a throwaway per-visitor database; never the real one.
if config.demo:
    st.info(
        "**Demo — synthetic data.** Everything here is generated; changes vanish when you leave.",
        icon=":material/science:",
    )
db_path = demo.demo_db_path() if config.demo else db.default_path()

# One connection per script run (SQLite is cheap to open); closed even if the page reruns or fails.
conn = db.connect(db_path)
categorize.seed_defaults(conn)
st.session_state[CONN_KEY] = conn
st.session_state[DB_PATH_KEY] = db_path
try:
    page.run()
finally:
    conn.close()
