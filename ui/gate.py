"""Passphrase screen shown before any page when EXPENSES_PASSPHRASE_HASH is set (spec 8.1)."""

import time

import streamlit as st

from expenses import auth

UNLOCKED_KEY = "_unlocked"


def require_passphrase(config: auth.Config) -> None:
    """Return if unlocked (or no gate configured); otherwise draw the lock screen and stop the run."""
    if not config.passphrase_hash or st.session_state.get(UNLOCKED_KEY):
        return

    _, middle, _ = st.columns([1, 1.3, 1])
    with middle:
        st.html('<div style="height: 12vh"></div>')
        with st.container(border=True):
            st.image("ui/assets/icon.svg", width=44)
            st.subheader("This tracker is locked")
            st.caption("Enter the passphrase to see the dashboard.")
            passphrase = st.text_input("Passphrase", type="password", key="passphrase")
            if st.button(
                "Unlock", key="unlock", type="primary", icon=":material/lock_open:", width="stretch"
            ):
                if auth.verify_passphrase(passphrase, config.passphrase_hash):
                    st.session_state[UNLOCKED_KEY] = True
                    st.rerun()
                time.sleep(0.5)  # slows down guessing
                st.error("Wrong passphrase.", icon=":material/lock:")
    st.stop()
