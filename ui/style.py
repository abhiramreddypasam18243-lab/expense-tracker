"""Injects the design tokens + stylesheet for the active theme. No external assets (offline rule)."""

import streamlit as st

from ui import theme


def inject() -> None:
    st.html(theme.css(theme.current()))
