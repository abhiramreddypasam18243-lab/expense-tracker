"""Step 25 — light theme by default (AC-130)."""

import re

from ui import style


def test_ac_130_light_default_script():
    js = style.LIGHT_DEFAULT_JS
    assert "et-light-default-v1" in js  # once per browser
    # Only a stored "System" choice is changed, and only to "Light".
    assert """=== '"System"') { localStorage.setItem(k, '"Light"')""" in js
    assert "changed && dark" in js and "location.reload()" in js  # reload only when dark is showing
    assert not re.search(r"fetch\(|XMLHttpRequest|https?://", js)  # no network


def test_ac_130_injected_on_every_page(monkeypatch):
    calls = []
    monkeypatch.setattr(style.st, "html", lambda body, **kw: calls.append((body, kw)))
    monkeypatch.setattr(style.st, "button", lambda *a, **k: False)
    style.inject()
    assert any(body == style.LIGHT_DEFAULT_JS and kw.get("unsafe_allow_javascript") for body, kw in calls)
