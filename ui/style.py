"""Injects the design tokens + stylesheet, and keeps Python-drawn colours in step with the theme.

No external assets (offline rule).

The stylesheet follows Streamlit's theme by itself (CSS light-dark()). Charts, sparklines and
category colours are drawn in Python with the theme the script saw, and Streamlit does not rerun
the script when the viewer switches System/Light/Dark in the ⋮ menu (streamlit issue #11920).
THEME_SYNC_JS watches the colour scheme Streamlit sets on the app; when it no longer matches the
mode this run drew with, it clicks a hidden button once, which reruns the page in the new theme.
"""

import streamlit as st

from ui import theme

SYNC_KEY = "theme-sync"

# Runs in the page itself (st.html is not iframed). Self-contained: no network, no storage.
THEME_SYNC_JS = """
<script>
(() => {
  const drawn = "__MODE__";
  const app = document.querySelector('[data-testid="stApp"]');
  if (!app) return;
  const shown = () => (getComputedStyle(app).colorScheme.includes("dark") ? "dark" : "light");
  const check = () => {
    const now = shown();
    if (now === drawn) { window.__etThemeAsked = null; return; }
    if (window.__etThemeAsked === now) return;  // ask once per switch: never loops
    window.__etThemeAsked = now;
    const button = document.querySelector(".st-key-__KEY__ button");
    if (button) button.click();
  };
  if (window.__etThemeObserver) window.__etThemeObserver.disconnect();
  window.__etThemeObserver = new MutationObserver(check);
  window.__etThemeObserver.observe(app, { attributes: true, attributeFilter: ["class", "style"] });
  setTimeout(check, 300);
})();
</script>
"""


def inject() -> None:
    st.html(theme.css())
    with st.container(key=SYNC_KEY):  # hidden by the stylesheet (takes no space); the button just reruns
        st.button("Refresh colours for the current theme", key=f"{SYNC_KEY}-button")
        st.html(
            THEME_SYNC_JS.replace("__MODE__", theme.current()).replace("__KEY__", SYNC_KEY),
            unsafe_allow_javascript=True,
        )
