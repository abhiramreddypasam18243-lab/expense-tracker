"""Streamlit entry point. Step 1: walking-skeleton page only."""

import streamlit as st

from expenses import __version__

st.set_page_config(page_title="Expense Tracker", page_icon="💸", layout="wide")

st.title("Expense Tracker")
st.caption(f"Private, offline expense tracking · v{__version__}")
st.info("Walking skeleton is running. Import, insights and dashboards arrive in later steps.")
