"""Visual building blocks: icons, KPI cards with sparklines, section titles, list rows, story banner.

Pure HTML strings (rendered with st.html) styled by ui/theme.py. User data is always escaped.
"""

from __future__ import annotations

import base64
import html
import itertools
from dataclasses import dataclass

# Small stroke icons (24x24 viewBox), drawn inline so no icon font or CDN is needed.
_ICON_PATHS = {
    "spend": '<path d="M7 17 17 7"/><path d="M8 7h9v9"/>',
    "income": '<path d="M17 7 7 17"/><path d="M16 17H7V8"/>',
    "net": '<path d="M3 12h4l3 8 4-16 3 8h4"/>',
    "count": '<path d="M4 3h16v18l-3-2-3 2-3-2-3 2-2-2z"/><path d="M8 8h8M8 12h8M8 16h5"/>',
    "pie": '<path d="M21 12A9 9 0 1 1 12 3v9z"/><path d="M15 3.5A9 9 0 0 1 20.5 9H15z"/>',
    "bars": '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    "target": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
    "alert": '<path d="M12 3 2 21h20z"/><path d="M12 10v5M12 18h.01"/>',
    "repeat": '<path d="M17 2l4 4-4 4"/><path d="M3 11V9a3 3 0 0 1 3-3h15"/><path d="M7 22l-4-4 4-4"/>'
    '<path d="M21 13v2a3 3 0 0 1-3 3H3"/>',
    "store": '<path d="M3 9l1.5-5h15L21 9"/><path d="M4 9v11h16V9"/><path d="M3 9h18"/><path d="M9 20v-6h6v6"/>',
    "sparkle": '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/>',
    "upload": '<path d="M12 16V4"/><path d="M7 9l5-5 5 5"/><path d="M4 20h16"/>',
    "history": '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l3 2"/>',
    "calendar": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "scale": '<path d="M12 3v18M5 7h14"/><path d="M5 7l-3 7a3 3 0 0 0 6 0zM19 7l-3 7a3 3 0 0 0 6 0z"/>',
}


def svg_img(svg: str, width: int, height: int) -> str:
    """Streamlit's HTML sanitiser strips inline <svg>, but allows images: embed the SVG as a data URI."""
    if "xmlns=" not in svg:
        svg = svg.replace("<svg ", '<svg xmlns="http://www.w3.org/2000/svg" ', 1)
    data = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f'<img src="data:image/svg+xml;base64,{data}" width="{width}" height="{height}" alt="">'


def icon(name: str, size: int = 18, color: str = "#6366F1") -> str:
    svg = (
        f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="{color}" '
        f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{_ICON_PATHS[name]}</svg>'
    )
    return svg_img(svg, size, size)


def esc(text: object) -> str:
    return html.escape(str(text))


_ids = itertools.count()


def sparkline(values: list[int], color: str, width: int = 120, height: int = 34) -> str:
    """Tiny trend line with a soft area fill. Display only: values are scaled to pixels."""
    if len(values) < 2 or max(values) == min(values):
        values = [0, 0] if len(values) < 2 else values
    low, high = min(values), max(values)
    span = (high - low) or 1
    step = width / (len(values) - 1)
    points = [(i * step, height - 3 - (v - low) / span * (height - 6)) for i, v in enumerate(values)]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    area = f"0,{height} {line} {width},{height}"
    gid = f"spark{next(_ids)}"
    last_x, last_y = points[-1]
    return svg_img(
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        f'<defs><linearGradient id="{gid}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{color}" stop-opacity="0.28"/>'
        f'<stop offset="1" stop-color="{color}" stop-opacity="0"/></linearGradient></defs>'
        f'<polygon points="{area}" fill="url(#{gid})"/>'
        f'<polyline points="{line}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round" '
        f'stroke-linecap="round"/><circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="2.6" fill="{color}"/></svg>',
        width,
        height,
    )


@dataclass
class Kpi:
    label: str
    value: str
    icon: str
    color: str  # icon + sparkline colour
    spark: list[int]
    delta: str | None = None  # e.g. "▲ 12%"
    tone: str = "flat"  # good | bad | flat
    vs: str = ""


def kpi_grid(cards: list[Kpi]) -> str:
    tiles = []
    for k in cards:
        delta = f'<span class="delta {k.tone}">{esc(k.delta)}</span>' if k.delta else ""
        vs = f'<div class="vs">{esc(k.vs)}</div>' if k.vs else ""
        tiles.append(
            f'<div class="kpi"><div class="top"><span class="label">{esc(k.label)}</span>'
            f'<span class="icon" style="background:{k.color}1f;color:{k.color}">{icon(k.icon, color=k.color)}</span></div>'
            f'<div class="value">{esc(k.value)}</div>'
            f'<div class="foot"><div>{delta}{vs}</div>{sparkline(k.spark, k.color)}</div></div>'
        )
    return f'<div class="kpi-grid">{"".join(tiles)}</div>'


def section_title(text: str, icon_name: str, sub: str | None = None) -> str:
    out = f'<div class="section-title"><span class="chip">{icon(icon_name, 16)}</span>{esc(text)}</div>'
    return out + (f'<div class="section-sub">{esc(sub)}</div>' if sub else "")


def story(label: str, text_html: str) -> str:
    """Gradient banner. text_html must already be escaped where it contains user data."""
    return (
        f'<div class="story"><div class="label">{esc(label)}</div><div class="text">{text_html}</div></div>'
    )


def list_row(
    title: str, meta: str, side_html: str = "", icon_name: str = "sparkle", color: str = "#6366F1"
) -> str:
    return (
        f'<div class="list-row"><span class="badge-ic" style="background:{color}1f;color:{color}">'
        f"{icon(icon_name, color=color)}</span>"
        f'<div class="main"><div class="item-title">{esc(title)}</div><div class="item-meta">{esc(meta)}</div></div>'
        f'<div class="side">{side_html}</div></div>'
    )


EMPTY_ILLUSTRATION = """<svg width="150" height="110" viewBox="0 0 150 110">
<defs><linearGradient id="eg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#6366F1"/>
<stop offset=".55" stop-color="#8B5CF6"/><stop offset="1" stop-color="#EC4899"/></linearGradient></defs>
<ellipse cx="75" cy="100" rx="52" ry="7" fill="#6366F1" opacity=".10"/>
<rect x="28" y="22" width="94" height="66" rx="14" fill="url(#eg)" opacity=".95"/>
<rect x="38" y="34" width="44" height="7" rx="3.5" fill="#fff" opacity=".9"/>
<rect x="38" y="47" width="70" height="6" rx="3" fill="#fff" opacity=".55"/>
<rect x="38" y="59" width="58" height="6" rx="3" fill="#fff" opacity=".55"/>
<circle cx="112" cy="26" r="13" fill="#fff"/><path d="M112 20v12M106 26h12" stroke="#8B5CF6" stroke-width="3"
stroke-linecap="round"/></svg>"""


def empty_html(title: str, body: str) -> str:
    return f'<div class="empty">{svg_img(EMPTY_ILLUSTRATION, 150, 110)}<h3>{esc(title)}</h3><p>{esc(body)}</p></div>'
