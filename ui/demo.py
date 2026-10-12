"""Demo mode (EXPENSES_DEMO=1): every visitor gets a private, throwaway database of synthetic data."""

import os
import tempfile
from pathlib import Path

import streamlit as st

from expenses import categorize, db, insights
from expenses.importer import import_csv
from tools.make_synthetic import generate_csv

DEMO_DB_KEY = "_demo_db"
DEMO_SEED = 42


def no_network(currency, on):
    return None  # synthetic data is INR-only; never fetch rates in demo mode


def import_synthetic(conn, seed: int, account: str):
    return import_csv(
        conn,
        generate_csv(seed, "debit_credit"),
        account=account,
        source_name=f"synthetic_seed_{seed}.csv",
        fetch_rate=no_network,
    )


def demo_db_path() -> str:
    """Path of this session's demo database, created and seeded on first use."""
    path = st.session_state.get(DEMO_DB_KEY)
    if path and Path(path).exists():
        return path
    fd, path = tempfile.mkstemp(prefix="expenses-demo-", suffix=".db")
    os.close(fd)
    conn = db.connect(path)
    try:
        categorize.seed_defaults(conn)
        import_synthetic(conn, DEMO_SEED, "Demo Savings")
        for name, limit in (("Food & Dining", 12_000_00), ("Groceries", 10_000_00), ("Shopping", 25_000_00)):
            insights.set_budget(conn, categorize.category_id(conn, name), limit)
    finally:
        conn.close()
    st.session_state[DEMO_DB_KEY] = path
    return path
