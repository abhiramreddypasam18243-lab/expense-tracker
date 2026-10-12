"""Altair charts (ships with Streamlit). Clean: no gridlines, labelled, theme-aware."""

from __future__ import annotations

import altair as alt
import pandas as pd

from expenses.insights import CategoryTotal
from expenses.money import format_inr
from ui.common import compact_inr, month_label


def category_donut(totals: list[CategoryTotal], spend_minor: int) -> alt.Chart:
    df = pd.DataFrame(
        {
            "Category": [t.name for t in totals],
            "spend": [t.spend_minor for t in totals],
            "Amount": [format_inr(t.spend_minor) for t in totals],
            "Share": [f"{t.spend_minor * 100 // spend_minor}%" for t in totals],
        }
    )
    scale = alt.Scale(domain=[t.name for t in totals], range=[t.color for t in totals])
    return (
        alt.Chart(df)
        .mark_arc(innerRadius=52, outerRadius=88, cornerRadius=3, padAngle=0.012)
        .encode(
            theta=alt.Theta("spend:Q", stack=True),
            order=alt.Order("spend:Q", sort="descending"),
            color=alt.Color("Category:N", scale=scale, legend=None),
            tooltip=["Category:N", "Amount:N", "Share:N"],
        )
        .properties(height=200)
    )


def spend_trend(trend: list[tuple[str, int]], selected: str) -> alt.Chart:
    df = pd.DataFrame(
        {
            "Month": [month_label(m, short=True) for m, _ in trend],
            "spend": [s // 100 for _, s in trend],  # whole rupees: axis scale only
            "Amount": [format_inr(s) for _, s in trend],
            "label": [compact_inr(s) if s else "" for _, s in trend],
            "selected": [m == selected for m, _ in trend],
        }
    )
    order = list(df["Month"])
    base = alt.Chart(df).encode(
        x=alt.X("Month:N", sort=order, title=None, axis=alt.Axis(labelAngle=0, ticks=False, domain=False)),
        y=alt.Y("spend:Q", title=None, axis=None),
    )
    bars = base.mark_bar(cornerRadiusTopLeft=5, cornerRadiusTopRight=5, size=26).encode(
        color=alt.condition("datum.selected", alt.value("#4F46E5"), alt.value("#A5B4FC")),
        tooltip=["Month:N", "Amount:N"],
    )
    labels = base.mark_text(dy=-8, fontSize=11, color="#94A3B8").encode(text="label:N")
    return (bars + labels).properties(height=250).configure_view(strokeWidth=0)


def merchant_bars(merchants: list[tuple[str, int]]) -> alt.Chart:
    df = pd.DataFrame(
        {
            "Merchant": [m for m, _ in merchants],
            "spend": [s // 100 for _, s in merchants],
            "Amount": [format_inr(s) for _, s in merchants],
        }
    )
    base = alt.Chart(df).encode(
        y=alt.Y(
            "Merchant:N", sort="-x", title=None, axis=alt.Axis(ticks=False, domain=False, labelLimit=180)
        ),
        # Headroom on the right so the amount labels are never clipped.
        x=alt.X(
            "spend:Q", title=None, axis=None, scale=alt.Scale(domain=[0, max(df["spend"].max() * 3 // 2, 1)])
        ),
    )
    bars = base.mark_bar(cornerRadiusTopRight=5, cornerRadiusBottomRight=5, size=20, color="#6366F1").encode(
        tooltip=["Merchant:N", "Amount:N"]
    )
    labels = base.mark_text(align="left", dx=6, fontSize=12, color="#94A3B8").encode(text="Amount:N")
    return (bars + labels).properties(height=max(120, 44 * len(df))).configure_view(strokeWidth=0)
