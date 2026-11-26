"""Streamlit dashboard for stored transactions.

Run from the project root:

    streamlit run src/personal_expense_analytics_engine/_dashboard.py

Everything is calculated locally from the SQLite database. The optional AI insight
sends only computed summary facts to the local Ollama service.
"""

import os
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from personal_expense_analytics_engine._analyst import (
    _previous_month,
    monthly_spending_insight,
    question_insight,
)
from personal_expense_analytics_engine._categorization import CATEGORY_NAMES
from personal_expense_analytics_engine._dashboard_data import (
    OTHER_CATEGORY,
    accounts,
    category_changes,
    category_frame,
    category_order,
    currencies,
    fold_categories,
    monthly_frame,
    recurring_frame,
    save_category_edits,
    transactions_frame,
    unusual_frame,
)
from personal_expense_analytics_engine._ollama import (
    DEFAULT_MODEL,
    OllamaAnalystError,
)
from personal_expense_analytics_engine._storage import connect

DEFAULT_DATABASE = os.environ.get("EXPENSES_DB", "data/expenses.db")
ALL_ACCOUNTS = "All accounts"


@dataclass(frozen=True)
class Palette:
    """Chart colours for one theme (validated categorical order, fixed slots)."""

    series: tuple[str, ...]
    other: str
    surface: str
    text_secondary: str
    grid: str
    baseline: str


LIGHT = Palette(
    series=(
        "#257de0",
        "#f47230",
        "#00b894",
        "#f2a600",
        "#ee7fac",
        "#008a17",
        "#6a4ce2",
    ),
    other="#a3a3a3",
    surface="#ffffff",
    text_secondary="#666666",
    grid="#e7e7e7",
    baseline="#c2c2c2",
)
DARK = Palette(
    series=(
        "#338ced",
        "#e76624",
        "#00a888",
        "#d28e05",
        "#e24d8b",
        "#0aac23",
        "#9887ef",
    ),
    other="#6b6b6b",
    surface="#0e1117",
    text_secondary="#9c9c9c",
    grid="#303030",
    baseline="#474747",
)


# ---------------------------------------------------------------- data loading


@dataclass(frozen=True)
class DashboardData:
    """Everything the page shows for one database, account and currency."""

    monthly: pd.DataFrame
    categories: pd.DataFrame
    transactions: pd.DataFrame
    recurring: pd.DataFrame
    unusual: pd.DataFrame


@st.cache_data(show_spinner=False)
def _load_scope(database: str, _mtime: float) -> tuple[list[str], dict[str, list[str]]]:
    """Return accounts and the currencies available per account scope."""
    with closing(connect(database)) as conn:
        account_ids = accounts(conn)
        scopes = {ALL_ACCOUNTS: currencies(conn)}
        scopes.update({acc: currencies(conn, acc) for acc in account_ids})
    return account_ids, scopes


@st.cache_data(show_spinner="Crunching transactions…")
def _load_data(
    database: str, _mtime: float, currency: str, account_id: str | None, today: date
) -> DashboardData:
    """Load all frames for the page. ``_mtime`` busts the cache when the DB changes."""
    with closing(connect(database)) as conn:
        return DashboardData(
            monthly=monthly_frame(conn, currency, account_id=account_id, today=today),
            categories=category_frame(conn, currency, account_id=account_id),
            transactions=transactions_frame(conn, currency, account_id=account_id),
            recurring=recurring_frame(conn, currency, account_id=account_id),
            unusual=unusual_frame(conn, currency, account_id=account_id),
        )


# ---------------------------------------------------------------- chart helpers


def _palette() -> Palette:
    theme = getattr(st.context, "theme", None)
    return DARK if theme is not None and theme.type == "dark" else LIGHT


def _base_layout(
    fig: go.Figure, palette: Palette, *, height: int, months_on_x: bool = True
) -> go.Figure:
    fig.update_layout(
        height=height,
        margin={"l": 8, "r": 8, "t": 8, "b": 8},
        barcornerradius=4,
        hovermode="closest",
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "x": 0,
            "title": None,
        },
        font={"family": 'system-ui, -apple-system, "Segoe UI", sans-serif'},
    )
    if months_on_x:
        # Month labels like "2026-08*" must stay categories, not be parsed as dates.
        fig.update_xaxes(
            showgrid=False, linecolor=palette.baseline, title=None, type="category"
        )
        fig.update_yaxes(gridcolor=palette.grid, zeroline=False, title=None)
    else:
        fig.update_xaxes(gridcolor=palette.grid, zeroline=False, title=None)
        fig.update_yaxes(showgrid=False, linecolor=palette.baseline, title=None)
    return fig


def _month_label(month: str, partial: bool) -> str:
    return f"{month}*" if partial else month


def _monthly_chart(monthly: pd.DataFrame, currency: str, palette: Palette) -> go.Figure:
    labels = [_month_label(m, p) for m, p in zip(monthly["month"], monthly["partial"])]
    opacity = [0.45 if p else 1.0 for p in monthly["partial"]]
    fig = go.Figure()
    for column, name, color in (
        ("spent", "Spending", palette.series[0]),
        ("income", "Income", palette.series[1]),
    ):
        fig.add_bar(
            x=labels,
            y=monthly[column],
            name=name,
            marker={
                "color": color,
                "opacity": opacity,
                "line": {"width": 0},
            },
            hovertemplate=f"<b>%{{x}}</b><br>{name}: %{{y:,.2f}} {currency}<extra></extra>",
        )
    fig.update_layout(barmode="group", bargap=0.35, bargroupgap=0.08)
    fig.update_yaxes(ticksuffix=f" {currency}", tickformat=",.0f")
    return _base_layout(fig, palette, height=340)


def _category_rank_chart(
    month_categories: pd.DataFrame, currency: str, palette: Palette
) -> go.Figure:
    data = month_categories.sort_values("spent")
    fig = go.Figure(
        go.Bar(
            x=data["spent"],
            y=data["category"],
            orientation="h",
            marker={
                "color": palette.series[0],
                "line": {"width": 0},
            },
            text=[f"{v:,.0f}" for v in data["spent"]],
            textposition="outside",
            cliponaxis=False,
            customdata=data["count"],
            hovertemplate=(
                "<b>%{y}</b><br>%{x:,.2f} " + currency + "<br>%{customdata} purchases"
                "<extra></extra>"
            ),
        )
    )
    fig.update_layout(bargap=0.3)
    # Headroom so the value label on the longest bar is not clipped.
    fig.update_xaxes(tickformat=",.0f", range=[0, _column_max(data, "spent") * 1.18])
    return _base_layout(
        fig, palette, height=max(220, 34 * len(data) + 40), months_on_x=False
    )


def _category_trend_chart(
    folded: pd.DataFrame,
    monthly: pd.DataFrame,
    order: list[str],
    currency: str,
    colors: dict[str, str],
    palette: Palette,
) -> go.Figure:
    partial = dict(zip(monthly["month"], monthly["partial"]))
    months = sorted(folded["month"].unique())
    labels = [_month_label(m, bool(partial.get(m))) for m in months]
    present = set(folded["category"])
    series = [c for c in order if c in present]
    if OTHER_CATEGORY in present and OTHER_CATEGORY not in series:
        series.append(OTHER_CATEGORY)
    fig = go.Figure()
    for category in series:
        values = (
            folded[folded["category"] == category]
            .set_index("month")["spent"]
            .reindex(months, fill_value=0.0)
        )
        fig.add_bar(
            x=labels,
            y=values,
            name=category,
            marker={
                "color": colors.get(category, palette.other),
                "line": {"width": 2, "color": palette.surface},
            },
            hovertemplate=(
                f"<b>{category}</b> · %{{x}}<br>%{{y:,.2f}} {currency}<extra></extra>"
            ),
        )
    fig.update_yaxes(ticksuffix=f" {currency}", tickformat=",.0f")
    fig = _base_layout(fig, palette, height=380)
    # Square segments: rounded ends would split a stack into separate pills.
    fig.update_layout(
        barmode="stack", bargap=0.45, barcornerradius=0, legend_traceorder="normal"
    )
    return fig


# ---------------------------------------------------------------- page sections


def _kpis(data: DashboardData, month: str, previous: str | None, currency: str) -> None:
    by_month: dict[str, dict[str, Any]] = {
        str(record["month"]): {str(k): v for k, v in record.items()}
        for record in data.monthly.to_dict("records")
    }
    row = by_month[month]
    prev = by_month.get(previous) if previous is not None else None
    recurring_total = _column_total(data.recurring, "per_month")
    in_month = data.transactions["date"].astype(str).str.startswith(month)
    month_transactions = _rows(data.transactions, in_month)
    spent_count = sum(
        bool(value)
        for value in month_transactions["counts_as_spending"].to_numpy()
    )
    income_count = sum(
        bool(direction == "credit" and category == "Income")
        for direction, category in zip(
            month_transactions["direction"].to_numpy(),
            month_transactions["category"].to_numpy(),
        )
    )

    def delta(column: str) -> str | None:
        if prev is None:
            return None
        return f"{row[column] - prev[column]:+,.2f} vs {previous}"

    cols = st.columns(4)

    with cols[0].container(border=True):
        st.metric(
            f"Spent ({currency}) | {spent_count} transaction{'s' if spent_count != 1 else ''}",
            f"{row['spent']:,.2f}",
            delta("spent"),
            delta_color="inverse",
        )

    with cols[1].container(border=True):
        st.metric(
        f"Income ({currency}) | {income_count} transaction{'s' if income_count != 1 else ''}",
        f"{row['income']:,.2f}", delta("income")
        )

    cols[2].metric(
        f"Net flow ({currency})", f"{row['net']:,.2f}", delta("net"), border=True
    )
    cols[3].metric(
        f"Recurring ≈ per month ({currency})",
        f"{recurring_total:,.2f}",
        border=True,
        help=(
            f"{len(data.recurring)} detected recurring payments; each typical "
            "amount scaled to one month."
        ),
    )


def _overview_tab(data: DashboardData, currency: str, palette: Palette) -> None:
    st.subheader("Spending and income by month")
    st.plotly_chart(
        _monthly_chart(data.monthly, currency, palette),
        width="stretch",
        config={"displayModeBar": False},
    )
    if bool(data.monthly["partial"].to_numpy().any()):
        st.caption(
            "\\* Incomplete month (data starts mid-month or the month is not over), "
            "shown lighter. Don't read its drop as a real change."
        )
    with st.expander("Monthly table"):
        st.dataframe(
            data.monthly,
            hide_index=True,
            width="stretch",
            column_config=_money_columns(currency, "spent", "income", "net"),
        )


def _categories_tab(
    data: DashboardData,
    month: str,
    previous: str | None,
    currency: str,
    palette: Palette,
) -> None:
    order = category_order(data.categories)
    named = [c for c in order if c != OTHER_CATEGORY][: len(palette.series)]
    colors = {name: palette.series[i] for i, name in enumerate(named)}
    colors[OTHER_CATEGORY] = palette.other

    month_categories = _rows(data.categories, data.categories["month"] == month)
    left, right = st.columns([2, 3], gap="large")
    with left:
        st.subheader(f"Where it went in {month}")
        if month_categories.empty:
            st.info("No spending recorded this month.")
        else:
            st.plotly_chart(
                _category_rank_chart(month_categories, currency, palette),
                width="stretch",
                config={"displayModeBar": False},
            )
    with right:
        st.subheader("Categories over time")
        folded = fold_categories(data.categories, order)
        st.plotly_chart(
            _category_trend_chart(
                folded, data.monthly, order, currency, colors, palette
            ),
            width="stretch",
            config={"displayModeBar": False},
        )

    if previous is None:
        st.caption("No earlier month stored, so there is nothing to compare against.")
        return
    st.subheader(f"Change vs {previous}")
    changes = category_changes(data.categories, month, previous)
    st.dataframe(
        changes,
        hide_index=True,
        width="stretch",
        column_config={
            "category": "Category",
            **_money_columns(currency, "spent", "previous", "change"),
            "spent": st.column_config.NumberColumn(month, format=f"%.2f {currency}"),
            "previous": st.column_config.NumberColumn(
                previous, format=f"%.2f {currency}"
            ),
            "change_pct": st.column_config.NumberColumn("Change %", format="%+.1f%%"),
        },
    )


def _recurring_tab(data: DashboardData, currency: str) -> None:
    st.subheader("Recurring payments")
    st.caption(
        "Merchants charged at a regular interval with a similar amount "
        "(at least three times)."
    )
    if data.recurring.empty:
        st.info("No recurring payments detected yet. More history helps.")
        return
    upcoming = data.recurring.sort_values("next_expected")
    st.dataframe(
        upcoming,
        hide_index=True,
        width="stretch",
        column_config={
            "merchant": "Merchant",
            "frequency": "Every",
            **_money_columns(currency, "typical_amount", "per_month"),
            "typical_amount": st.column_config.NumberColumn(
                "Typical amount", format=f"%.2f {currency}"
            ),
            "per_month": st.column_config.ProgressColumn(
                "≈ per month",
                format=f"%.2f {currency}",
                min_value=0.0,
                max_value=_column_max(data.recurring, "per_month"),
            ),
            "occurrences": "Seen",
            "last_date": st.column_config.DateColumn("Last charged"),
            "next_expected": st.column_config.DateColumn("Next expected"),
        },
    )


def _unusual_tab(data: DashboardData, month: str, currency: str) -> None:
    st.subheader("Unusually large purchases")
    st.caption(
        "Compared with the typical amount at the same merchant, or within the "
        "category when the merchant has too little history. Score is a robust "
        "z-score; 3.5+ is flagged."
    )
    if data.unusual.empty:
        st.success("Nothing unusual detected.")
        return
    only_month = st.toggle(f"Only {month}", value=False)
    frame = data.unusual
    if only_month:
        months = pd.to_datetime(frame["date"]).dt.strftime("%Y-%m")
        frame = _rows(frame, months == month)
    st.dataframe(
        frame,
        hide_index=True,
        width="stretch",
        column_config={
            "date": st.column_config.DateColumn("Date"),
            "description": "Description",
            "category": "Category",
            **_money_columns(currency, "amount", "typical"),
            "times_typical": st.column_config.NumberColumn("× typical", format="%.1f×"),
            "score": st.column_config.NumberColumn("Score", format="%.1f"),
            "compared_with": "Compared with",
        },
    )


def _transactions_tab(
    data: DashboardData,
    currency: str,
    database: str,
    account_id: str | None,
) -> None:
    st.subheader("Transactions")
    frame = data.transactions
    filters = st.columns([2, 2, 1])
    query = filters[0].text_input("Search description or merchant", "")
    chosen = filters[1].multiselect(
        "Categories", sorted(frame["category"].unique()), placeholder="All"
    )
    direction = filters[2].selectbox("Direction", ["All", "debit", "credit"])
    if query:
        text = frame["description"] + " " + frame["merchant"]
        frame = _rows(frame, text.str.contains(query, case=False, regex=False))
    if chosen:
        frame = _rows(frame, frame["category"].isin(chosen))
    if direction != "All":
        frame = _rows(frame, frame["direction"] == direction)
    st.caption(f"{len(frame)} of {len(data.transactions)} transactions")
    categories = sorted(
        (set(CATEGORY_NAMES) | set(frame["category"])) - {"Uncategorized"}
    )
    category_options = ["Uncategorized", *categories]
    original = frame.copy()
    scope = f"{database}\0{account_id}\0{currency}\0"
    scope += "\0".join(frame["transaction_id"].astype(str))
    scope_key = sha256(scope.encode("utf-8")).hexdigest()[:16]
    version_key = f"category-editor-version-{scope_key}"
    notice_key = f"category-editor-notice-{scope_key}"
    version = st.session_state.get(version_key, 0)
    editor_key = f"category-editor-{scope_key}-{version}"

    notice = st.session_state.pop(notice_key, None)
    if notice:
        st.success(notice)

    with st.form(f"category-form-{scope_key}-{version}"):
        edited = st.data_editor(
            frame,
            hide_index=True,
            width="stretch",
            height=480,
            column_order=[
                "date",
                "description",
                "merchant",
                "category",
                "direction",
                "amount",
                "counts_as_spending",
            ],
            disabled=[
                "transaction_id",
                "date",
                "description",
                "merchant",
                "direction",
                "amount",
                "counts_as_spending",
            ],
            column_config={
                "date": st.column_config.DateColumn("Date"),
                "description": "Description",
                "merchant": "Merchant",
                "category": st.column_config.SelectboxColumn(
                    "Category",
                    options=category_options,
                    required=True,
                    help="Choose a category, or select Uncategorized to clear it.",
                ),
                "direction": "Direction",
                **_money_columns(currency, "amount"),
                "counts_as_spending": st.column_config.CheckboxColumn(
                    "Counts as spending",
                    help="Debits except Transfer and Income categories.",
                ),
            },
            key=editor_key,
        )
        save = st.form_submit_button("Save category changes", type="primary")

    if save:
        with closing(connect(database)) as conn:
            changed_count = save_category_edits(conn, original, edited)
        if changed_count:
            st.session_state[version_key] = version + 1
            st.session_state[notice_key] = (
                f"Saved categories for {changed_count} transaction(s)."
            )
            st.cache_data.clear()
            st.rerun()
        else:
            st.info("No category changes to save.")


def _legacy_insight_tab(
    database: str, month: str, currency: str, account_id: str | None
) -> None:
    st.subheader("AI spending insight")
    st.caption(
        "Figures are computed locally; only a redacted qualitative summary is sent "
        "to Ollama. Amounts and transaction details stay in this dashboard."
    )
    model = st.text_input("Ollama model", DEFAULT_MODEL)
    key = f"insight|{database}|{month}|{currency}|{account_id}|{model}"
    if st.button("Generate insight", type="primary"):
        with st.spinner(f"Asking {model}…"), closing(connect(database)) as conn:
            try:
                st.session_state[key] = monthly_spending_insight(
                    conn, month, currency, account_id=account_id, model=model
                )
            except OllamaAnalystError as exc:
                st.error(
                    f"Ollama is not available: {exc}\n\n"
                    f"Start Ollama and run `ollama pull {model}`, then try again."
                )
            except ValueError as exc:
                st.warning(str(exc))
    insight = st.session_state.get(key)
    if insight is not None:
        st.markdown(insight.text)
        with st.expander("Verified facts the model was given"):
            st.text(insight.facts_text)

    st.divider()
    st.subheader("Ask a privacy-safe question")
    st.caption(
        "Calculations happen locally. Factual answers never use Ollama; advice sends "
        "only category names and qualitative facts, never amounts or transactions."
    )
    question = st.text_input(
        "Your question",
        placeholder=(
            "Compare 2026-10 and 2026-11, biggest category, recurring payments, "
            "or how can I save money?"
        ),
    )
    question_key = (
        f"question|{database}|{month}|{currency}|{account_id}|{model}|{question}"
    )
    if st.button("Ask", type="primary", disabled=not question.strip()):
        with st.spinner("Preparing a privacy-safe answer…"), closing(connect(database)) as conn:
            try:
                st.session_state[question_key] = question_insight(
                    conn,
                    question,
                    month,
                    currency,
                    account_id=account_id,
                    model=model,
                )
            except OllamaAnalystError as exc:
                st.error(
                    f"Ollama is not available: {exc}\n\n"
                    f"Start Ollama and run `ollama pull {model}`, then try again."
                )
            except ValueError as exc:
                st.warning(str(exc))
    answer = st.session_state.get(question_key)
    if answer is not None:
        st.markdown(answer.text)
        with st.expander("Privacy-safe facts used"):
            st.text(answer.facts_text)


def _insight_tab(
    database: str, month: str, currency: str, account_id: str | None
) -> None:
    """Render one conversational, privacy-safe question window."""
    st.subheader("Ask about your spending")
    st.caption(
        "Ask questions in one window. Calculations happen locally; only a redacted "
        "qualitative summary is sent to Ollama for advice."
    )
    model = st.text_input("Ollama model", DEFAULT_MODEL)
    history_key = f"question-history|{database}|{month}|{currency}|{account_id}"
    history = st.session_state.setdefault(history_key, [])
    for item in history:
        with st.chat_message("user"):
            st.write(item["question"])
        with st.chat_message("assistant"):
            st.markdown(item["answer"])
            with st.expander("Privacy-safe facts used"):
                st.text(item["facts"])

    question = st.chat_input(
        "Ask: compare months, biggest purchase, spending total, recurring payments…"
    )
    if not question:
        return

    with st.chat_message("user"):
        st.write(question)
    with st.chat_message("assistant"):
        with st.spinner("Preparing a privacy-safe answer…"), closing(
            connect(database)
        ) as conn:
            try:
                answer = question_insight(
                    conn,
                    question,
                    month,
                    currency,
                    account_id=account_id,
                    model=model,
                )
                st.markdown(answer.text)
                with st.expander("Privacy-safe facts used"):
                    st.text(answer.facts_text)
                history.append(
                    {
                        "question": question,
                        "answer": answer.text,
                        "facts": answer.facts_text,
                    }
                )
            except OllamaAnalystError as exc:
                st.error(
                    f"Ollama is not available: {exc}\n\n"
                    f"Start Ollama and run `ollama pull {model}`, then try again."
                )
            except ValueError as exc:
                st.warning(str(exc))


def _rows(frame: pd.DataFrame, mask: Any) -> pd.DataFrame:
    """Filter rows by a boolean mask (typed wrapper; pandas stubs are loose here)."""
    return cast(pd.DataFrame, frame.loc[mask])


def _column_max(frame: pd.DataFrame, column: str) -> float:
    return float(frame[column].to_numpy().max()) if len(frame) else 0.0


def _column_total(frame: pd.DataFrame, column: str) -> float:
    return float(frame[column].to_numpy().sum())


def _money_columns(currency: str, *names: str) -> dict[str, Any]:
    return {
        name: st.column_config.NumberColumn(
            name.replace("_", " ").capitalize(), format=f"%.2f {currency}"
        )
        for name in names
    }


# ---------------------------------------------------------------- page


def main() -> None:
    """Render the dashboard page."""
    st.set_page_config(page_title="Expense analytics", page_icon="📊", layout="wide")

    with st.sidebar:
        st.header("Data")
        database = st.text_input("SQLite database", DEFAULT_DATABASE)
        path = Path(database)
        if not path.is_file():
            st.error(f"Database not found: `{database}`")
            st.info(
                "Import transactions first with `just authorize …`, or point to "
                "another file."
            )
            st.stop()
        mtime = path.stat().st_mtime
        account_ids, scopes = _load_scope(database, mtime)
        account = st.selectbox(
            "Account",
            [ALL_ACCOUNTS, *account_ids],
            disabled=len(account_ids) < 2,
        )
        account_id = None if account == ALL_ACCOUNTS else account
        available = scopes.get(account, [])
        if not available:
            st.warning("No transactions stored yet.")
            st.stop()
        currency = st.selectbox(
            "Currency",
            available,
            help="Currencies are never added together; pick one at a time.",
        )
        currency = currency or available[0]
        if st.button("Reload data", width="stretch"):
            st.cache_data.clear()

    today = date.today()
    data = _load_data(database, mtime, currency, account_id, today)
    if data.monthly.empty:
        st.info(f"No {currency} transactions stored.")
        st.stop()

    months = list(data.monthly["month"])
    partial = dict(zip(data.monthly["month"], data.monthly["partial"]))
    complete = [m for m in months if not partial[m]]
    default_month = complete[-1] if complete else months[-1]
    with st.sidebar:
        month = st.selectbox(
            "Month",
            list(reversed(months)),
            index=list(reversed(months)).index(default_month),
            format_func=lambda m: f"{m} (incomplete)" if partial[m] else m,
        )
        month = month or default_month
        st.divider()
        first = data.transactions["date"].min()
        last = data.transactions["date"].max()
        st.caption(
            f"{len(data.transactions)} transactions · {first} → {last}\n\n"
            "All analysis runs locally."
        )

    previous_month = _previous_month(month)
    previous = previous_month if previous_month in partial else None

    st.title("Expense analytics")
    if partial[month]:
        st.warning(
            f"{month} is incomplete, so totals and comparisons understate it.",
            icon="⚠️",
        )
    _kpis(data, month, previous, currency)

    palette = _palette()
    tabs = st.tabs(
        ["Overview", "Categories", "Recurring", "Unusual", "Transactions", "AI insight"]
    )
    with tabs[0]:
        _overview_tab(data, currency, palette)
    with tabs[1]:
        _categories_tab(data, month, previous, currency, palette)
    with tabs[2]:
        _recurring_tab(data, currency)
    with tabs[3]:
        _unusual_tab(data, month, currency)
    with tabs[4]:
        _transactions_tab(data, currency, database, account_id)
    with tabs[5]:
        _insight_tab(database, month, currency, account_id)


main()
