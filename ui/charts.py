"""Altair charts (ships with Streamlit), styled with the design tokens and validated colours.

Data-viz rules applied: colour follows the category (validated 8-hue palette, the rest folds into
"Other"), 2px surface gaps between slices, rounded bar ends on the baseline, no gridlines,
labelled legend next to the donut, hover tooltips everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import altair as alt
import pandas as pd

from expenses.insights import CategoryTotal
from expenses.money import format_inr
from ui import theme
from ui.common import compact_inr, month_label


@dataclass
class DonutRow:
    name: str
    color: str
    spend_minor: int
    parts: list[str] = field(default_factory=list)  # for "Other": the categories folded into it


def donut_rows(totals: list[CategoryTotal], mode: str) -> list[DonutRow]:
    """Core categories keep their own colour; every other category folds into one grey 'Other'."""
    rows: list[DonutRow] = []
    other = DonutRow("Other", theme.OTHER_COLOR[mode], 0)
    for t in totals:
        if t.name in theme.CATEGORY_PALETTE:
            rows.append(DonutRow(t.name, theme.category_color(t.name, mode), t.spend_minor))
        else:
            other.spend_minor += t.spend_minor
            other.parts.append(t.name)
    return rows + ([other] if other.spend_minor else [])


def category_donut(rows: list[DonutRow], spend_minor: int, mode: str) -> alt.LayerChart:
    tok = theme.tokens(mode)
    df = pd.DataFrame(
        {
            "Category": [r.name for r in rows],
            "spend": [r.spend_minor for r in rows],
            "Amount": [format_inr(r.spend_minor) for r in rows],
            "Share": [f"{r.spend_minor * 100 // max(spend_minor, 1)}%" for r in rows],
            "order": list(range(len(rows))),
        }
    )
    scale = alt.Scale(domain=[r.name for r in rows], range=[r.color for r in rows])
    arcs = (
        alt.Chart(df)
        .mark_arc(
            innerRadius=70,
            outerRadius=104,
            cornerRadius=5,
            padAngle=0.018,
            stroke=tok["--surface"],
            strokeWidth=2,
        )
        .encode(
            theta=alt.Theta("spend:Q", stack=True),
            order=alt.Order("order:Q"),
            color=alt.Color("Category:N", scale=scale, legend=None),
            tooltip=["Category:N", "Amount:N", "Share:N"],
        )
    )
    centre = pd.DataFrame({"line": [compact_inr(spend_minor), "spent"], "dy": [-6, 16], "size": [24, 12]})
    big = (
        alt.Chart(centre.iloc[[0]])
        .mark_text(fontSize=24, fontWeight=800, dy=-6, color=tok["--text"], font="Plus Jakarta Sans")
        .encode(text="line:N")
    )
    small = (
        alt.Chart(centre.iloc[[1]]).mark_text(fontSize=12, dy=16, color=tok["--muted"]).encode(text="line:N")
    )
    return (arcs + big + small).properties(height=230, background="transparent").configure_view(strokeWidth=0)


def _gradient(top: str, bottom: str) -> alt.Gradient:
    return alt.Gradient(
        gradient="linear",
        x1=1,
        x2=1,
        y1=1,
        y2=0,
        stops=[alt.GradientStop(color=bottom, offset=0), alt.GradientStop(color=top, offset=1)],
    )


def spend_trend(trend: list[tuple[str, int]], selected: str, mode: str) -> alt.LayerChart:
    tok = theme.tokens(mode)
    df = pd.DataFrame(
        {
            "Month": [month_label(m, short=True) for m, _ in trend],
            "spend": [s // 100 for _, s in trend],  # whole rupees: bar height only
            "Amount": [format_inr(s) for _, s in trend],
            # Label only the selected and the highest month, so labels never collide.
            "label": [
                compact_inr(s) if s and (m == selected or s == max(v for _, v in trend)) else ""
                for m, s in trend
            ],
            "selected": [m == selected for m, _ in trend],
        }
    )
    order = list(df["Month"])
    base = alt.Chart(df).encode(
        x=alt.X(
            "Month:N",
            sort=order,
            title=None,
            axis=alt.Axis(
                labelAngle=0, ticks=False, domain=False, labelColor=tok["--muted"], labelFontSize=11
            ),
        ),
        y=alt.Y(
            "spend:Q",
            title=None,
            axis=None,
            scale=alt.Scale(domain=[0, max(int(df["spend"].max()) * 6 // 5, 1)]),
        ),
    )
    others = (
        base.transform_filter("!datum.selected")
        .mark_bar(
            cornerRadiusTopLeft=6,
            cornerRadiusTopRight=6,
            size=24,
            color=_gradient("#A5B4FC", "#C7D2FE") if mode == "light" else _gradient("#4F55A8", "#363B7A"),
            opacity=0.75,
        )
        .encode(tooltip=["Month:N", "Amount:N"])
    )
    chosen = (
        base.transform_filter("datum.selected")
        .mark_bar(
            cornerRadiusTopLeft=6, cornerRadiusTopRight=6, size=24, color=_gradient("#8B5CF6", "#6366F1")
        )
        .encode(tooltip=["Month:N", "Amount:N"])
    )
    labels = base.mark_text(dy=-9, fontSize=11, fontWeight=600, color=tok["--muted"]).encode(text="label:N")
    return (
        (others + chosen + labels)
        .properties(height=250, background="transparent")
        .configure_view(strokeWidth=0)
    )


def merchant_bars(merchants: list[tuple[str, int]], mode: str = "light") -> alt.LayerChart:
    tok = theme.tokens(mode)
    df = pd.DataFrame(
        {
            "Merchant": [m for m, _ in merchants],
            "spend": [s // 100 for _, s in merchants],
            "Amount": [format_inr(s) for _, s in merchants],
        }
    )
    base = alt.Chart(df).encode(
        y=alt.Y(
            "Merchant:N",
            sort="-x",
            title=None,
            axis=alt.Axis(
                ticks=False, domain=False, labelLimit=180, labelColor=tok["--text"], labelFontSize=12
            ),
        ),
        # Headroom on the right so the amount labels are never clipped.
        x=alt.X(
            "spend:Q",
            title=None,
            axis=None,
            scale=alt.Scale(domain=[0, max(int(df["spend"].max()) * 3 // 2, 1)]),
        ),
    )
    bars = base.mark_bar(
        cornerRadiusTopRight=6,
        cornerRadiusBottomRight=6,
        size=18,
        color=alt.Gradient(
            gradient="linear",
            x1=0,
            x2=1,
            y1=1,
            y2=1,
            stops=[alt.GradientStop(color="#6366F1", offset=0), alt.GradientStop(color="#A855F7", offset=1)],
        ),
    ).encode(tooltip=["Merchant:N", "Amount:N"])
    labels = base.mark_text(align="left", dx=8, fontSize=12, fontWeight=600, color=tok["--muted"]).encode(
        text="Amount:N"
    )
    return (
        (bars + labels)
        .properties(height=max(130, 46 * len(df)), background="transparent")
        .configure_view(strokeWidth=0)
    )


def forecast_line(
    cumulative: list[tuple[int, int]], day: int, days: int, projected_total: int, mode: str
) -> alt.LayerChart:
    """Cumulative spend this month: actual up to the as-of day (solid), the projection to month end
    (dashed, same hue: it continues the same series) and, in a replay, what really happened (muted).

    One y-axis (rupees), recessive axes, legend on top, direct label on the projection's end,
    crosshair + tooltip on hover.
    """
    tok = theme.tokens(mode)
    spent_now = dict(cumulative).get(day, 0)
    rows = [("Spent so far", d, v) for d, v in cumulative if d <= day]
    rows += [("Projected", day, spent_now), ("Projected", days, projected_total)]
    rows += [
        ("What really happened", d, v)
        for d, v in cumulative
        if d >= day and cumulative[-1][0] == days and days > day
    ]
    df = pd.DataFrame(rows, columns=["Series", "Day", "paise"])
    df["rupees"] = df["paise"] // 100
    df["Amount"] = [format_inr(v) for v in df["paise"]]
    series = ["Spent so far", "Projected", "What really happened"]
    present = [s for s in series if s in set(df["Series"])]
    colors = {
        "Spent so far": tok["--accent"],
        "Projected": tok["--accent"],
        "What really happened": tok["--muted"],
    }
    color = alt.Color(
        "Series:N",
        scale=alt.Scale(domain=present, range=[colors[s] for s in present]),
        legend=alt.Legend(
            orient="top", title=None, labelColor=tok["--muted"], symbolStrokeWidth=2, symbolType="stroke"
        ),
    )
    dash = alt.StrokeDash(
        "Series:N",
        scale=alt.Scale(domain=present, range=[[6, 4] if s == "Projected" else [1, 0] for s in present]),
        legend=None,
    )
    top = max(int(df["rupees"].max()) * 11 // 10, 1)
    base = alt.Chart(df).encode(
        x=alt.X(
            "Day:Q",
            scale=alt.Scale(domain=[1, days], nice=False),
            axis=alt.Axis(
                title=None, tickCount=6, grid=False, domain=False, ticks=False, labelColor=tok["--muted"]
            ),
        ),
        y=alt.Y(
            "rupees:Q",
            scale=alt.Scale(domain=[0, top]),
            axis=alt.Axis(
                title=None,
                grid=True,
                gridColor=tok["--track"],
                domain=False,
                ticks=False,
                tickCount=4,
                labelColor=tok["--muted"],
                labelExpr="'₹' + format(datum.value, ',.0f')",
            ),
        ),
    )
    lines = base.mark_line(strokeWidth=2, interpolate="monotone").encode(color=color, strokeDash=dash)
    end = df[(df["Series"] == "Projected") & (df["Day"] == days)].assign(
        label=f"{compact_inr(projected_total)} projected"
    )
    label = (
        alt.Chart(end)
        .mark_text(align="right", dx=-4, dy=-10, fontSize=12, fontWeight=600, color=tok["--text"])
        .encode(x="Day:Q", y="rupees:Q", text="label:N")
    )
    hover = alt.selection_point(
        fields=["Day"], nearest=True, on="pointerover", empty=False, clear="pointerout"
    )
    targets = (
        base.mark_point(size=400, opacity=0)
        .encode(tooltip=["Series:N", "Day:Q", "Amount:N"])
        .add_params(hover)
    )
    dots = base.mark_point(size=70, filled=True, stroke=tok["--surface"], strokeWidth=2).encode(
        color=color, opacity=alt.condition(hover, alt.value(1), alt.value(0))
    )
    rule = (
        base.mark_rule(color=tok["--muted"], strokeWidth=1)
        .encode(opacity=alt.condition(hover, alt.value(0.6), alt.value(0)))
        .transform_filter(hover)
    )
    return (
        (lines + rule + dots + targets + label)
        .properties(height=340, background="transparent")
        .configure_view(strokeWidth=0)
    )
