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
    config = auth.load_config(tls_enabled=bool(st.get_option("server.sslCertFile")))
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
    st.Page("ui/forecast_page.py", title="Forecast", url_path="forecast", icon=":material/trending_up:"),
    st.Page("ui/ca.py", title="Your CA", url_path="ca", icon=":material/account_balance:"),
    st.Page("ui/settings.py", title="Settings", url_path="settings", icon=":material/tune:"),
]

with st.sidebar:
    if config.demo:
        st.caption(f"Web version · nothing is saved on the server · v{__version__}")
    else:
        st.caption(f"Private · your data stays on this machine · v{__version__}")

page = st.navigation(pages)

if config.demo:
    # 3a. Web mode: this visitor's private in-memory database; never a file on the server.
    conn = demo.session_conn()
    db_path = ":memory:"
    if demo.is_sample():
        text_col, button_col = st.columns([4, 1.3], vertical_alignment="center")
        text_col.info(
            "You're viewing **sample data**. "
            "Your own statements are processed in memory for this visit only.",
            icon=":material/science:",
        )
        if button_col.button("Use my own statements", key="use_own", type="primary", width="stretch"):
            demo.use_own_statements()
            st.rerun()
    else:
        st.info(
            "**Private session** — your statements are processed in memory for this visit only. "
            "Nothing is saved.",
            icon=":material/lock:",
        )
else:
    # 3b. Desktop: one connection per script run (SQLite is cheap to open); closed after the page.
    db_path = db.default_path()
    conn = db.connect(db_path)
    categorize.seed_defaults(conn)

st.session_state[CONN_KEY] = conn
st.session_state[DB_PATH_KEY] = db_path
try:
    page.run()
finally:
    if not config.demo:  # the web session's database must live on between reruns
        conn.close()
