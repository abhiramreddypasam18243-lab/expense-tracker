"""Settings: categories, keyword rules, smart categories, budgets and saved PDF passwords."""

import pandas as pd
import streamlit as st

from expenses import categorize, insights, learn, vault
from expenses.money import MoneyParseError, format_inr, parse_amount
from ui import theme
from ui.common import card, esc, get_conn, page_header

KINDS = ["expense", "income", "transfer"]

conn = get_conn()
mode = theme.current()


def dot_color(name: str, stored: str) -> str:
    """Core categories use the validated chart palette; user-made ones keep their own colour."""
    return theme.category_color(name, mode) if name in theme.CATEGORY_PALETTE else stored


page_header(
    "Settings",
    "Tune categories and rules, set monthly budgets, and manage saved PDF passwords.",
    eyebrow_text="Preferences",
)

categories = categorize.list_categories(conn)
by_name = {c["name"]: c for c in categories}
by_id = {c["id"]: c for c in categories}

tab_categories, tab_rules, tab_smart, tab_budgets, tab_passwords = st.tabs(
    [
        ":material/category: Categories",
        ":material/rule: Rules",
        ":material/auto_awesome: Smart categories",
        ":material/savings: Budgets",
        ":material/key: PDF passwords",
    ]
)

# --- categories ----------------------------------------------------------------------------------------
with tab_categories:
    list_col, form_col = st.columns([3, 2])
    with list_col, card("categories"):
        for c in categories:
            name_col, kind_col, action_col = st.columns([3, 1.4, 1], vertical_alignment="center")
            name_col.html(
                f'<div class="legend-row" style="border:none"><span class="dot" style="background:{esc(dot_color(c["name"], c["color"]))}">'
                f'</span><span class="name">{esc(c["name"])}</span></div>'
            )
            kind_col.caption(c["kind"].title())
            if c["name"] != categorize.UNCATEGORISED:
                with action_col.popover("", icon=":material/delete:", help=f"Delete {c['name']}"):
                    st.markdown(
                        f"Delete **{c['name']}**? Its transactions move to *Uncategorised* and its rules "
                        "and budget are removed."
                    )
                    if st.button("Delete category", key=f"del-cat-{c['id']}", type="primary"):
                        categorize.delete_category(conn, c["id"])
                        st.toast(f"Deleted {c['name']}", icon=":material/delete:")
                        st.rerun()
    with form_col, card("category-form"):
        st.markdown("**Add a category**")
        with st.form("add_category", clear_on_submit=True, border=False):
            name = st.text_input("Name", placeholder="e.g. Pets")
            kind = st.selectbox("Type", KINDS, format_func=str.title)
            color = st.color_picker("Colour", "#64748B")
            if st.form_submit_button("Add category", type="primary", icon=":material/add:"):
                if not name.strip():
                    st.error("Enter a name.")
                elif name.strip() in by_name:
                    st.error("That category already exists.")
                else:
                    categorize.add_category(conn, name, kind, color.upper())
                    st.toast(f"Added {name.strip()}", icon=":material/check:")
                    st.rerun()

# --- rules -------------------------------------------------------------------------------------------------
with tab_rules:
    st.caption(
        "A rule matches when its text appears anywhere in a description (case-insensitive). "
        "Lower priority numbers run first; the first match wins. Your manual choices are never overwritten."
    )
    rules = categorize.list_rules(conn)
    rules_df = pd.DataFrame(
        {
            "id": [r["id"] for r in rules],
            "Pattern": [r["pattern"] for r in rules],
            "Category": [by_id[r["category_id"]]["name"] for r in rules],
            "Priority": [r["priority"] for r in rules],
        }
    )
    version = st.session_state.setdefault("rules_version", 0)
    edited = st.data_editor(
        rules_df,
        key=f"rules-{version}",
        num_rows="dynamic",
        hide_index=True,
        width="stretch",
        height=420,
        column_order=["Priority", "Pattern", "Category"],
        column_config={
            "Priority": st.column_config.NumberColumn(
                min_value=0, max_value=9999, step=1, width="small", required=True, default=100
            ),
            "Pattern": st.column_config.TextColumn(required=True, width="large"),
            "Category": st.column_config.SelectboxColumn(
                options=list(by_name), required=True, width="medium"
            ),
        },
    )
    save_col, rerun_col, _ = st.columns([1, 1.4, 2])
    if save_col.button("Save rules", type="primary", icon=":material/save:", width="stretch"):
        original = {int(r["id"]): r for r in rules}
        kept_ids = set()
        problems = 0
        for row in edited.itertuples(index=False):
            if (
                not isinstance(row.Pattern, str)
                or not row.Pattern.strip()
                or row.Category not in by_name
                or pd.isna(row.Priority)
            ):
                problems += 1
                continue
            values = (row.Pattern, by_name[row.Category]["id"], int(row.Priority))
            if pd.isna(row.id):
                categorize.add_rule(conn, *values)
                continue
            rule_id = int(row.id)
            kept_ids.add(rule_id)
            old = original[rule_id]
            if (old["pattern"], old["category_id"], old["priority"]) != values:
                categorize.update_rule(conn, rule_id, *values)
        for rule_id in set(original) - kept_ids:
            categorize.delete_rule(conn, rule_id)
        st.session_state["rules_version"] = version + 1
        if problems:
            st.session_state["rules_warning"] = f"{problems} incomplete row(s) were skipped."
        st.toast("Rules saved", icon=":material/check:")
        st.rerun()
    if rerun_col.button("Re-run rules and smart categories", icon=":material/autorenew:", width="stretch"):
        changed = categorize.recategorize(conn)
        st.toast(f"Recategorised {changed} transaction(s)", icon=":material/autorenew:")
    if warning := st.session_state.pop("rules_warning", None):
        st.warning(warning, icon=":material/warning:")

# --- budgets ------------------------------------------------------------------------------------------------
with tab_budgets:
    list_col, form_col = st.columns([3, 2])
    budgets = {b["category_id"]: b for b in insights.list_budgets(conn)}
    with list_col, card("budgets"):
        if not budgets:
            st.caption("No budgets yet. Add one on the right; progress shows on the Dashboard.")
        for b in budgets.values():
            name_col, amount_col, action_col = st.columns([3, 2, 1], vertical_alignment="center")
            name_col.html(
                f'<div class="legend-row" style="border:none"><span class="dot" style="background:{esc(dot_color(b["name"], b["color"]))}">'
                f'</span><span class="name">{esc(b["name"])}</span></div>'
            )
            amount_col.markdown(f"**{format_inr(b['monthly_limit_minor'])}** / month")
            if action_col.button(
                "",
                icon=":material/close:",
                key=f"del-budget-{b['category_id']}",
                help=f"Remove the {b['name']} budget",
            ):
                insights.remove_budget(conn, b["category_id"])
                st.rerun()
    with form_col, card("budget-form"):
        st.markdown("**Set a monthly budget**")
        expense_names = [c["name"] for c in categories if c["kind"] == "expense"]
        with st.form("set_budget", clear_on_submit=True, border=False):
            category = st.selectbox("Category", expense_names)
            amount = st.text_input("Monthly limit (₹)", placeholder="e.g. 5,000")
            if st.form_submit_button("Save budget", type="primary", icon=":material/save:"):
                try:
                    limit = parse_amount(amount)
                    insights.set_budget(conn, by_name[category]["id"], limit)
                    st.toast(f"{category}: {format_inr(limit)} per month", icon=":material/check:")
                    st.rerun()
                except MoneyParseError:
                    st.error("Enter an amount like 5,000 or 5000.50")
                except ValueError as exc:
                    st.error(str(exc).capitalize())

# --- remembered PDF passwords (I12) -------------------------------------------------------------------
with tab_passwords, card("pdf-passwords"):
    saved = vault.accounts(conn)
    st.markdown("**Saved PDF passwords**")
    st.caption(
        "When a password opens a locked PDF statement, it's remembered for that account, so next month's "
        "statement opens without typing. They're stored encrypted on this computer and never shown."
    )
    if not saved:
        st.caption(
            "None saved yet. Type a PDF's password on the Import page and it's remembered after importing."
        )
    for account in saved:
        name_col, action_col = st.columns([5, 1], vertical_alignment="center")
        name_col.markdown(f":material/lock: **{esc(account)}**")
        if action_col.button("Forget", key=f"forget-pw-{account}", help=f"Forget the password for {account}"):
            vault.forget(conn, account)
            st.toast(f"Forgot the PDF password for {account}", icon=":material/check:")
            st.rerun()
    if len(saved) > 1 and st.button("Forget all", icon=":material/delete:"):
        vault.forget_all(conn)
        st.toast("Forgot all saved PDF passwords", icon=":material/check:")
        st.rerun()


# --- smart categories (spec 5.1) ----------------------------------------------------------------------
def pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.0f}%"


with tab_smart:
    enabled, threshold = learn.settings(conn)
    model = learn.model_for(conn)
    control_col, info_col = st.columns([2, 3])
    with control_col, card("smart-controls"):
        st.markdown("**Smart categories**")
        st.caption(
            "When no rule matches, a model trained on **your** categories suggests one. "
            "It learns from every category you set by hand."
        )
        on = st.toggle("Suggest categories", value=enabled, key="smart-on")
        level = st.slider(
            "Only when at least this sure",
            min_value=learn.THRESHOLD_RANGE[0],
            max_value=learn.THRESHOLD_RANGE[1],
            value=float(threshold),
            step=0.05,
            format="%.2f",
            disabled=not on,
            help="Higher = fewer suggestions, but more of them right. Below it, rows stay Uncategorised.",
        )
        if on != enabled or abs(level - threshold) > 1e-9:
            learn.set_enabled(conn, on)
            learn.set_threshold(conn, round(level, 2))
            st.rerun()
        if st.button(
            "Apply to my transactions", icon=":material/auto_awesome:", width="stretch", disabled=not on
        ):
            changed = categorize.recategorize(conn)
            st.toast(
                f"Updated {changed} transaction(s). Your own choices were kept.", icon=":material/check:"
            )
        if model is None:
            st.info(
                f"Not ready yet: it needs at least {learn.MIN_EXAMPLES} categorised transactions in "
                f"{learn.MIN_CATEGORIES}+ categories. Set some categories on the Transactions page.",
                icon=":material/hourglass_empty:",
            )
        else:
            st.caption(f"Learned from {model.n_examples} transactions in {len(model.labels)} categories.")

    with info_col, card("smart-score"):
        st.markdown("**How well it works on your data**")
        st.caption(
            "Tested on merchants it has **never seen**: it learns from 75 % of your merchants and is scored "
            "on the other 25 %."
        )
        try:
            ev = learn.evaluate_db(conn)
        except learn.NotEnoughData as exc:
            st.caption(f"Not enough data to score yet — {exc}.")
        else:
            m1, m2, m3 = st.columns(3)
            m1.metric("Right on new merchants", pct(ev.accuracy), help="If it always had to guess")
            m2.metric(f"Labelled at {ev.threshold:.2f}", pct(ev.coverage), help="The rest stay Uncategorised")
            m3.metric("Of those, right", pct(ev.accuracy_at_threshold))
            st.caption(
                f"{ev.n_train} transactions from {ev.train_merchants} merchants to learn · "
                f"{ev.n_test} from {ev.test_merchants} other merchants to test."
            )
            with st.expander("Details: thresholds, categories, learning curve", icon=":material/analytics:"):
                st.markdown("**Confidence threshold trade-off**")
                st.dataframe(
                    pd.DataFrame(
                        {
                            "Threshold": [f"{x:.2f}" for x, _, _ in ev.thresholds],
                            "Rows labelled": [c for _, c, _ in ev.thresholds],
                            "Right": [a if a is not None else 0.0 for _, _, a in ev.thresholds],
                        }
                    ),
                    hide_index=True,
                    width="stretch",
                    column_config={
                        "Rows labelled": st.column_config.ProgressColumn(
                            format="percent", min_value=0, max_value=1
                        ),
                        "Right": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1),
                    },
                )
                st.markdown("**Per category**")
                st.dataframe(
                    pd.DataFrame(
                        {
                            "Category": [s.name for s in ev.per_category],
                            "Precision": [s.precision for s in ev.per_category],
                            "Recall": [s.recall for s in ev.per_category],
                            "Test rows": [s.support for s in ev.per_category],
                        }
                    ),
                    hide_index=True,
                    width="stretch",
                    column_config={
                        "Precision": st.column_config.ProgressColumn(
                            format="percent",
                            min_value=0,
                            max_value=1,
                            help="When it says this category, how often it's right",
                        ),
                        "Recall": st.column_config.ProgressColumn(
                            format="percent",
                            min_value=0,
                            max_value=1,
                            help="How many of this category it finds",
                        ),
                    },
                )
                st.markdown("**Learning curve** — accuracy as it learns from more of your transactions")
                st.dataframe(
                    pd.DataFrame(
                        {
                            "Examples": [n for n, _ in ev.curve],
                            "Right on new merchants": [a for _, a in ev.curve],
                        }
                    ),
                    hide_index=True,
                    width="stretch",
                    column_config={
                        "Right on new merchants": st.column_config.ProgressColumn(
                            format="percent", min_value=0, max_value=1
                        )
                    },
                )
