"""Web mode (EXPENSES_DEMO=1, the hosted site): one private in-memory database per visitor.

Nothing is written to disk. The database lives in the visitor's session and disappears with it.
A new session starts with sample (synthetic) data; "Use my own statements" empties it.
"""

import sqlite3

import streamlit as st

from expenses import categorize, db, insights
from expenses.importer import import_csv
from tools.make_synthetic import generate_csv

SESSION_CONN_KEY = "_web_conn"
SAMPLE_KEY = "_web_sample"
DEMO_SEED = 42
# Session keys that belong to the old data and must go when the data is cleared.
_STALE_KEYS = ("last_report", "month", "plan", "plan_sig")


def no_network(currency, on):
    return None  # synthetic data is INR-only; never fetch rates for it


def import_synthetic(conn: sqlite3.Connection, seed: int, account: str):
    return import_csv(
        conn,
        generate_csv(seed, "debit_credit"),
        account=account,
        source_name=f"synthetic_seed_{seed}.csv",
        fetch_rate=no_network,
    )


def _new_database(with_sample: bool) -> sqlite3.Connection:
    conn = db.connect(":memory:", shared_across_threads=True)
    categorize.seed_defaults(conn)
    if with_sample:
        import_synthetic(conn, DEMO_SEED, "Demo Savings")
        for name, limit in (("Food & Dining", 12_000_00), ("Groceries", 10_000_00), ("Shopping", 25_000_00)):
            insights.set_budget(conn, categorize.category_id(conn, name), limit)
    return conn


def session_conn() -> sqlite3.Connection:
    """This visitor's in-memory database, created (with sample data) on first use."""
    conn = st.session_state.get(SESSION_CONN_KEY)
    if conn is None:
        conn = _new_database(with_sample=True)
        st.session_state[SESSION_CONN_KEY] = conn
        st.session_state[SAMPLE_KEY] = True
    return conn


def is_sample() -> bool:
    return bool(st.session_state.get(SAMPLE_KEY, False))


def use_own_statements() -> None:
    """Throw the sample data away: a fresh, empty in-memory database for this visitor."""
    old = st.session_state.pop(SESSION_CONN_KEY, None)
    if old is not None:
        old.close()
    st.session_state[SESSION_CONN_KEY] = _new_database(with_sample=False)
    st.session_state[SAMPLE_KEY] = False
    for key in _STALE_KEYS:
        st.session_state.pop(key, None)
