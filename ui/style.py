"""Injects the design tokens + stylesheet, and keeps Python-drawn colours in step with the theme.

No external assets (offline rule).

The stylesheet follows Streamlit's theme by itself (CSS light-dark()). Charts, sparklines and
category colours are drawn in Python with the theme the script saw, and Streamlit does not rerun
the script when the viewer switches System/Light/Dark in the ⋮ menu (streamlit issue #11920).
THEME_SYNC_JS watches the colour scheme Streamlit sets on the app; when it no longer matches the
mode this run drew with, it clicks a hidden button once, which reruns the page in the new theme.

LIGHT_DEFAULT_JS makes Light the default: Streamlit starts every new browser on "System" (dark on a
dark-mode device), so on a browser's first visit it switches Streamlit's stored choice to "Light"
once. Whatever the viewer picks in Settings after that is kept.
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


# Runs once per browser. Streamlit keeps the viewer's theme choice in localStorage under
# "stActiveTheme-<base path>-v2" (Streamlit 1.65). Our own marker records that the default was applied.
LIGHT_DEFAULT_JS = """
<script>
(() => {
  const MARK = "et-light-default-v1";
  let tries = 0;
  const apply = () => {
    try {
      if (localStorage.getItem(MARK)) return;
      const keys = Object.keys(localStorage).filter((k) => /^stActiveTheme-.*-v2$/.test(k));
      if (!keys.length) { if (++tries < 20) setTimeout(apply, 250); return; }  // Streamlit hasn't saved yet
      localStorage.setItem(MARK, "1");
      let changed = false;
      for (const k of keys) {
        if (localStorage.getItem(k) === '"System"') { localStorage.setItem(k, '"Light"'); changed = true; }
      }
      const app = document.querySelector('[data-testid="stApp"]');
      const dark = app && getComputedStyle(app).colorScheme.includes("dark");
      if (changed && dark) location.reload();  // Streamlit reads the stored theme only at start-up
    } catch (e) { /* storage blocked: keep the system theme */ }
  };
  apply();
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
        st.html(LIGHT_DEFAULT_JS, unsafe_allow_javascript=True)
