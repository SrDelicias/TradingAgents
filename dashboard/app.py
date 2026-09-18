"""Local, read-only Streamlit dashboard for TradingAgents paper trading."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dashboard.components.layout import (  # noqa: E402
    render_activity,
    render_current_analysis,
    render_decision,
    render_equity_chart,
    render_header,
    render_kpis,
    render_performance,
    render_pipeline,
    render_positions,
    render_reasoning,
    render_risk,
    render_trades,
)
from dashboard.demo_data import build_demo_data  # noqa: E402
from dashboard.utils.data import (  # noqa: E402
    current_analysis,
    filter_by_symbol,
    load_equity_history,
    load_events,
    load_portfolio,
    pipeline_status,
)
from dashboard.utils.stats import trade_statistics  # noqa: E402
from tradingagents.default_config import DEFAULT_CONFIG  # noqa: E402
from tradingagents.paper_trading import PaperTradingConfig  # noqa: E402

DEMO_MODE = "--demo" in sys.argv
PAPER_CONFIG = PaperTradingConfig.from_mapping(DEFAULT_CONFIG)


def load_css() -> None:
    css_path = Path(__file__).parent / "styles" / "theme.css"
    st.markdown(f"<style>{css_path.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)


def read_state() -> tuple[dict, list[dict], list[dict]]:
    if DEMO_MODE:
        demo = build_demo_data()
        return demo["portfolio"], demo["equity_history"], demo["events"]
    return (
        load_portfolio(PAPER_CONFIG.portfolio_path),
        load_equity_history(PAPER_CONFIG.equity_history_path),
        load_events(PAPER_CONFIG.events_path),
    )


def reset_state() -> None:
    for path in (PAPER_CONFIG.portfolio_path, PAPER_CONFIG.equity_history_path):
        resolved = Path(path).resolve()
        if resolved.is_file():
            resolved.unlink()


st.set_page_config(
    page_title="TradingAgents · Paper Trading",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)
load_css()

with st.sidebar:
    st.markdown("### TradingAgents")
    st.caption("LOCAL MONITOR")
    section = st.radio(
        "View",
        ["Portfolio", "Agents", "Trades", "Performance", "Risk", "System"],
        label_visibility="collapsed",
    )
    initial_portfolio, _, initial_events = read_state()
    symbols = sorted(
        set(initial_portfolio.get("positions", {}))
        | {event.get("symbol") for event in initial_events if event.get("symbol")}
    )
    symbol_filter = st.selectbox("Symbol", ["All symbols", *symbols])
    if st.button("↻  REFRESH", width="stretch"):
        st.rerun()
    st.divider()
    if DEMO_MODE:
        st.info("Demo mode uses in-memory data only. Reset is disabled.")
    else:
        with st.expander("Reset portfolio"):
            st.warning("This removes the local simulated portfolio and its equity history.")
            confirmed = st.checkbox("I understand this cannot be undone")
            confirmation_text = st.text_input("Type RESET to confirm")
            if st.button(
                "RESET LOCAL PORTFOLIO",
                type="secondary",
                disabled=not (confirmed and confirmation_text == "RESET"),
                width="stretch",
            ):
                reset_state()
                st.success("Local paper portfolio reset.")
                st.rerun()
    st.caption("No exchange connectivity\n\nNo manual trading controls")


@st.fragment(run_every="2s")
def live_dashboard() -> None:
    portfolio, equity_history, events = read_state()
    scoped_events = filter_by_symbol(events, symbol_filter)
    analysis = current_analysis(scoped_events or events)
    pipeline = pipeline_status(scoped_events or events)
    stats = trade_statistics(portfolio.get("trade_history", []))

    render_header(analysis, events, DEMO_MODE)

    if section == "Portfolio":
        render_kpis(portfolio)
        chart_column, analysis_column = st.columns([1.8, 1], gap="medium")
        with chart_column:
            range_column, scale_column = st.columns([1.45, 1], gap="small")
            with range_column:
                timeframe = st.radio(
                    "Equity timeframe",
                    ["1H", "6H", "24H", "7D", "ALL"],
                    index=4,
                    horizontal=True,
                    label_visibility="collapsed",
                )
            with scale_column:
                scale_mode = st.radio(
                    "Equity scale",
                    ["AUTO SCALE", "FULL SCALE"],
                    horizontal=True,
                    label_visibility="collapsed",
                )
            render_equity_chart(
                equity_history,
                float(portfolio["initial_balance"]),
                timeframe,
                scale_mode,
            )
        with analysis_column:
            render_current_analysis(analysis)
            render_decision(analysis)
        render_positions(
            portfolio,
            symbol_filter,
            float(PAPER_CONFIG.max_position_percent),
        )
        render_reasoning(analysis)
        pipeline_column, activity_column = st.columns([0.8, 1.3], gap="medium")
        with pipeline_column:
            render_pipeline(pipeline)
        with activity_column:
            render_activity(scoped_events)
        render_trades(portfolio.get("trade_history", []), symbol_filter)
    elif section == "Agents":
        render_decision(analysis)
        render_reasoning(analysis)
        pipeline_column, activity_column = st.columns([0.8, 1.4], gap="medium")
        with pipeline_column:
            render_pipeline(pipeline)
        with activity_column:
            render_activity(scoped_events)
    elif section == "Trades":
        render_trades(portfolio.get("trade_history", []), symbol_filter)
    elif section == "Performance":
        render_kpis(portfolio)
        render_performance(portfolio, stats)
        timeframe = st.radio("Performance timeframe", ["1H", "6H", "24H", "7D", "ALL"], index=4, horizontal=True, label_visibility="collapsed")
        render_equity_chart(
            equity_history,
            float(portfolio["initial_balance"]),
            timeframe,
            "AUTO SCALE",
        )
    elif section == "Risk":
        render_risk(PAPER_CONFIG)
        render_positions(
            portfolio,
            symbol_filter,
            float(PAPER_CONFIG.max_position_percent),
        )
    else:
        render_current_analysis(analysis)
        render_activity(scoped_events)
        st.caption(
            f"Portfolio: {PAPER_CONFIG.portfolio_path}\n\n"
            f"Equity: {PAPER_CONFIG.equity_history_path}\n\n"
            f"Events: {PAPER_CONFIG.events_path}"
        )


live_dashboard()
