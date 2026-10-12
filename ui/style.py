"""Minimal CSS on top of the Streamlit theme. No external fonts, assets or CDNs (offline rule)."""

import streamlit as st

_CSS = """
<style>
/* Comfortable reading width and spacing */
[data-testid="stMainBlockContainer"], .block-container {
    max-width: 1200px; padding-top: 2.25rem; padding-bottom: 4rem;
}
[data-testid="stAppDeployButton"] { display: none; }

/* Page header */
.page-sub { margin-top: -0.6rem; margin-bottom: 0.4rem; opacity: 0.65; font-size: 0.98rem; }
.eyebrow {
    font-size: 0.72rem; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase;
    opacity: 0.6; margin-bottom: 0.35rem;
}

/* Metric cards */
[data-testid="stMetric"] label p { font-size: 0.85rem; opacity: 0.75; }
[data-testid="stMetricValue"] { font-variant-numeric: tabular-nums; }

/* Category legend next to the donut */
.legend-row {
    display: flex; align-items: center; gap: 0.55rem; padding: 0.32rem 0; font-size: 0.9rem;
    border-bottom: 1px solid rgba(148, 163, 184, 0.18);
}
.legend-row:last-child { border-bottom: none; }
.legend-row .dot { width: 0.65rem; height: 0.65rem; border-radius: 50%; flex: none; }
.legend-row .name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.legend-row .amt { margin-left: auto; font-variant-numeric: tabular-nums; font-weight: 600; }
.legend-row .pct { width: 2.6rem; text-align: right; opacity: 0.55; font-variant-numeric: tabular-nums; }

/* Lists of items inside cards (anomalies, budgets, import history) */
.item-title { font-weight: 600; font-size: 1.02rem; font-variant-numeric: tabular-nums; }
.item-meta { opacity: 0.65; font-size: 0.86rem; }
.amount-neg { font-variant-numeric: tabular-nums; }
.amount-pos { color: #16A34A; font-variant-numeric: tabular-nums; }

/* Budget bars, coloured by status */
.budget { padding: 0.45rem 0 0.75rem; }
.budget-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.4rem; }
.track { height: 0.55rem; border-radius: 999px; background: rgba(148, 163, 184, 0.22); overflow: hidden; }
.fill { height: 100%; border-radius: 999px; }
.fill.ok { background: #22C55E; }  .fill.warning { background: #F59E0B; }  .fill.over { background: #EF4444; }
.budget .item-meta { margin-top: 0.35rem; }
.pill { font-size: 0.78rem; font-weight: 600; padding: 0.12rem 0.55rem; border-radius: 999px; }
.pill.ok { color: #16A34A; background: rgba(34, 197, 94, 0.14); }
.pill.warning { color: #D97706; background: rgba(245, 158, 11, 0.16); }
.pill.over { color: #DC2626; background: rgba(239, 68, 68, 0.14); }

/* Empty states */
.empty { text-align: center; padding: 2.4rem 1rem 1.2rem; }
.empty .icon { font-size: 2.6rem; line-height: 1; margin-bottom: 0.6rem; }
.empty h3 { margin: 0 0 0.35rem 0; padding: 0; font-size: 1.25rem; }
.empty p { opacity: 0.68; margin: 0 auto; max-width: 30rem; }
</style>
"""


def inject() -> None:
    st.html(_CSS)
