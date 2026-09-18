"""Streamlit renderers for the live paper-trading dashboard."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape

import plotly.graph_objects as go
import streamlit as st

from dashboard.utils.formatting import (
    as_float,
    format_currency,
    format_elapsed,
    format_percent,
    format_quantity,
    format_time,
    parse_timestamp,
)


def _tone(value) -> str:
    number = as_float(value)
    return "positive" if number > 0 else "negative" if number < 0 else ""


def _next_analysis(events: list[dict]) -> str:
    for event in reversed(events):
        metadata = event.get("metadata") or {}
        if metadata.get("next_analysis_seconds") is not None:
            seconds = max(0, int(metadata["next_analysis_seconds"]))
            minutes, seconds = divmod(seconds, 60)
            return f"{minutes}m {seconds:02d}s"
        scheduled = parse_timestamp(metadata.get("next_analysis"))
        if scheduled:
            seconds = max(
                0,
                int((scheduled - datetime.now(timezone.utc)).total_seconds()),
            )
            minutes, seconds = divmod(seconds, 60)
            return f"{minutes}m {seconds:02d}s"
    return "Not scheduled"


def render_header(analysis: dict, events: list[dict], demo: bool) -> None:
    status = analysis["system_status"]
    if demo:
        status = "LIVE"
    last_update = events[-1].get("timestamp") if events else None
    completed_at = parse_timestamp(analysis.get("completed"))
    cycle_duration = format_elapsed(analysis.get("started"), completed_at)
    st.markdown(
        f"""
        <div class="ta-header">
          <div class="ta-brand-block"><div class="ta-brand">TradingAgents</div>
            <div class="ta-badges"><span class="mode-badge">PAPER TRADING</span><span class="safety-badge">NO REAL MONEY</span>{'<span class="demo-badge">DEMO</span>' if demo else ''}</div>
          </div>
          <div class="header-status"><div class="ta-live"><span class="ta-dot {status}"></span>{status}</div>
            <div class="header-metrics">
              <div><span>Last update</span><strong>{format_time(last_update)}</strong></div>
              <div><span>Cycle duration</span><strong>{cycle_duration}</strong></div>
              <div><span>Next analysis</span><strong>{_next_analysis(events)}</strong></div>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_kpis(portfolio: dict) -> None:
    items = [
        ("Total equity", format_currency(portfolio["total_equity"]), f"Initial {format_currency(portfolio['initial_balance'])}", ""),
        ("Cash", format_currency(portfolio["cash_balance"]), "Available", ""),
        ("Invested", format_currency(portfolio["positions_value"]), format_percent(as_float(portfolio["positions_value"]) / as_float(portfolio["total_equity"]) if as_float(portfolio["total_equity"]) else 0), ""),
        ("Total PnL", format_currency(portfolio["total_pnl"], sign=True), "Realized + unrealized", _tone(portfolio["total_pnl"])),
        ("Return", format_percent(portfolio["return_percent"], sign=True), "Since inception", _tone(portfolio["return_percent"])),
        ("Drawdown", format_percent(portfolio["current_drawdown_percent"]), f"Max {format_percent(portfolio['max_drawdown_percent'])}", "negative" if as_float(portfolio["current_drawdown_percent"]) else ""),
    ]
    cards = "".join(
        f'<div class="kpi-card"><div class="kpi-label">{label}</div><div class="kpi-value {tone}">{value}</div><div class="kpi-note">{note}</div></div>'
        for label, value, note, tone in items
    )
    st.markdown(f'<div class="kpi-grid">{cards}</div>', unsafe_allow_html=True)


def render_equity_chart(
    history: list[dict],
    initial_balance: float,
    timeframe: str,
    scale_mode: str = "AUTO SCALE",
) -> None:
    st.markdown('<div class="section-label">Portfolio value</div>', unsafe_allow_html=True)
    if not history:
        st.markdown('<div class="empty-state">Waiting for the first equity snapshot.</div>', unsafe_allow_html=True)
        return
    now = datetime.now(timezone.utc)
    windows = {"1H": timedelta(hours=1), "6H": timedelta(hours=6), "24H": timedelta(hours=24), "7D": timedelta(days=7)}
    filtered = history
    if timeframe in windows:
        cutoff = now - windows[timeframe]
        filtered = [item for item in history if parse_timestamp(item.get("timestamp")) >= cutoff]
    if not filtered:
        st.markdown(f'<div class="empty-state">No equity points in the selected {timeframe} window.</div>', unsafe_allow_html=True)
        return
    timestamps = [parse_timestamp(item["timestamp"]) for item in filtered]
    values = [as_float(item.get("total_equity")) for item in filtered]
    color = "#36d399" if values[-1] >= initial_balance else "#fb7185"
    figure = go.Figure()
    figure.add_trace(go.Scatter(x=timestamps, y=values, mode="lines", line={"color": color, "width": 2.3}, fill="tozeroy", fillcolor="rgba(54,211,153,.06)", name="Equity", hovertemplate="%{x|%d/%m/%Y %H:%M:%S}<br>%{y:.4f} €<extra></extra>"))
    figure.add_hline(y=initial_balance, line_dash="dot", line_color="#566174", annotation_text="Initial capital", annotation_font_color="#8491a3")
    y_axis = {"gridcolor": "rgba(80,95,115,.15)", "ticksuffix": " €"}
    if scale_mode == "AUTO SCALE":
        low = min([*values, initial_balance])
        high = max([*values, initial_balance])
        span = high - low
        if span == 0:
            padding = max(abs(initial_balance) * 0.01, 0.5)
        else:
            padding = max(span * 0.25, abs(initial_balance) * 0.0025)
        y_axis["range"] = [low - padding, high + padding]
    else:
        y_axis["range"] = [0, max([*values, initial_balance]) * 1.08]
    figure.update_layout(template="plotly_dark", height=280, margin={"l": 8, "r": 8, "t": 8, "b": 4}, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", showlegend=False, hovermode="x unified", xaxis={"showgrid": False, "rangeslider": {"visible": False}}, yaxis=y_axis)
    st.plotly_chart(figure, width="stretch", config={"displaylogo": False, "responsive": True})


def render_current_analysis(analysis: dict) -> None:
    price = format_currency(analysis["price"]) if analysis.get("price") is not None else "Waiting for price..."
    st.markdown(
        f"""<div class="ta-panel analysis-panel"><div class="section-label">Current analysis</div>
        <div class="analysis-top"><div class="analysis-symbol">{escape(str(analysis['symbol']))}</div><div class="analysis-price">{price}</div></div>
        <div class="analysis-grid">
          <div><div class="detail-label">Stage</div><div class="detail-value active">{escape(str(analysis['stage']))}</div></div>
          <div><div class="detail-label">Started</div><div class="detail-value">{format_time(analysis.get('started'))}</div></div>
          <div><div class="detail-label">Cycle</div><div class="detail-value">{format_elapsed(analysis.get('started'), parse_timestamp(analysis.get('completed')))}</div></div>
          <div><div class="detail-label">Decision</div><div class="detail-value warning">{escape(str(analysis['decision']))}</div></div>
        </div></div>""",
        unsafe_allow_html=True,
    )


def render_positions(
    portfolio: dict,
    symbol_filter: str | None = None,
    max_position_percent: float = 0.10,
) -> None:
    st.markdown('<div class="panel-title">OPEN POSITIONS</div>', unsafe_allow_html=True)
    positions = portfolio.get("positions", {})
    if symbol_filter and symbol_filter != "All symbols":
        positions = {key: value for key, value in positions.items() if key == symbol_filter}
    if not positions:
        st.markdown('<div class="empty-state">No open positions.</div>', unsafe_allow_html=True)
        return
    equity = as_float(portfolio.get("total_equity"))
    cards = []
    for symbol, position in positions.items():
        value = as_float(position.get("market_value"), as_float(position.get("quantity")) * as_float(position.get("current_price")))
        pnl = as_float(position.get("unrealized_pnl"))
        invested = as_float(position.get("invested_amount"))
        return_pct = as_float(position.get("return_percent"), pnl / invested if invested else 0)
        allocation = value / equity if equity else 0
        bar_width = min(100, allocation / max_position_percent * 100) if max_position_percent else 0
        cards.append(
            f"""<div class="position-card"><div class="position-head"><div class="position-symbol">{escape(symbol)}</div>{'<span class="stale-pill">STALE</span>' if position.get('price_stale') else ''}</div>
            <div class="allocation-head"><span>Allocation</span><strong>{format_percent(allocation)} / max {format_percent(max_position_percent, 0)}</strong></div>
            <div class="allocation-track"><div class="allocation-fill" style="width:{bar_width:.1f}%"></div></div>
            <div class="position-data">
              <div><div class="detail-label">Entry</div><div class="detail-value">{format_currency(position.get('entry_price'))}</div></div>
              <div><div class="detail-label">Current</div><div class="detail-value">{format_currency(position.get('current_price'))}</div></div>
              <div><div class="detail-label">Value</div><div class="detail-value position-value">{format_currency(value)}</div></div>
              <div><div class="detail-label">Quantity</div><div class="detail-value">{format_quantity(position.get('quantity'), symbol)}</div></div>
              <div><div class="detail-label">PnL €</div><div class="detail-value {_tone(pnl)}">{format_currency(pnl, sign=True)}</div></div>
              <div><div class="detail-label">PnL %</div><div class="detail-value {_tone(return_pct)}">{format_percent(return_pct, sign=True)}</div></div>
              <div><div class="detail-label">Stop loss</div><div class="detail-value negative">{format_currency(position.get('stop_loss_price'))}</div></div>
              <div><div class="detail-label">Take profit</div><div class="detail-value positive">{format_currency(position.get('take_profit_price'))}</div></div>
            </div></div>"""
        )
    st.markdown(f'<div class="positions-grid">{"".join(cards)}</div>', unsafe_allow_html=True)


def render_pipeline(rows: list[dict]) -> None:
    symbols = {"COMPLETED": "✓", "RUNNING": "●", "WARNING": "!", "REJECTED": "×", "ERROR": "×", "PENDING": "○"}
    content = []
    for index, row in enumerate(rows):
        status = row["status"]
        content.append(
            f'<div class="pipeline-row {status}"><span class="pipeline-icon {status}">{symbols.get(status, "○")}</span><span class="pipeline-name">{escape(row["stage"])}</span><span class="pipeline-status">{status}</span></div>'
        )
        if index < len(rows) - 1:
            content.append('<div class="pipeline-line"></div>')
    st.markdown(f'<div class="ta-panel"><div class="panel-title">AGENT PIPELINE</div>{"".join(content)}</div>', unsafe_allow_html=True)


def render_activity(events: list[dict]) -> None:
    visible = list(reversed(events[-50:]))
    if not visible:
        body = '<div class="empty-state">Waiting for system activity...</div>'
    else:
        rows = []
        for event in visible:
            event_type = str(event.get("type", ""))
            metadata = event.get("metadata") or {}
            if event_type == "ORDER_EXECUTED":
                badge = str(metadata.get("action") or "TRADE")
            elif event_type == "RISK_APPROVED":
                badge = "APPROVED"
            elif event_type == "RISK_REJECTED":
                badge = "REJECTED"
            elif event_type == "MARKET_PRICE_UPDATED":
                badge = "PRICE"
            elif event_type == "SYSTEM_STARTED":
                badge = "SYSTEM"
            elif event_type.startswith("POSITION_"):
                badge = str(metadata.get("action") or "POSITION")
            else:
                badge = "AGENT" if event_type.startswith("AGENT_") else "SYSTEM"
            rows.append(
                f'<div class="activity-row"><span class="activity-time">{format_time(event.get("timestamp"))}</span><span class="activity-badge {badge}">{escape(badge)}</span><span class="activity-message">{escape(str(event.get("message") or event_type))}</span></div>'
            )
        body = "".join(rows)
    st.markdown(f'<div class="ta-panel"><div class="panel-title">LIVE ACTIVITY</div><div class="activity-feed">{body}</div></div>', unsafe_allow_html=True)


def render_decision(analysis: dict) -> None:
    risk_tone = "positive" if analysis["risk"] == "APPROVED" else "negative" if analysis["risk"] == "REJECTED" else "warning"
    execution_tone = "positive" if analysis["execution"] == "EXECUTED" else "negative" if analysis["execution"] == "NOT EXECUTED" else "warning"
    reason = f'<div class="kpi-note negative">{escape(analysis.get("risk_reason", ""))}</div>' if analysis.get("risk_reason") and analysis["risk"] == "REJECTED" else ""
    amount = f'<div class="kpi-note">{format_currency(analysis.get("execution_value"))}</div>' if analysis.get("execution_value") is not None else ""
    st.markdown(
        f"""<div class="decision-card ta-panel"><div class="decision-heading"><span>CURRENT DECISION</span><strong>{escape(str(analysis['symbol']))}</strong></div><div class="decision-flow">
        <div class="decision-step"><div class="decision-name">Portfolio Manager</div><div class="decision-value warning">{escape(str(analysis['decision']))}</div></div>
        <div class="decision-arrow">↓</div>
        <div class="decision-step"><div class="decision-name">Normalized</div><div class="decision-value active">{escape(str(analysis['normalized']))}</div></div>
        <div class="decision-arrow">↓</div>
        <div class="decision-step"><div class="decision-name">Hard Risk Engine</div><div class="decision-value {risk_tone}">{escape(str(analysis['risk']))}</div>{reason}</div>
        <div class="decision-arrow">↓</div>
        <div class="decision-step"><div class="decision-name">Paper Broker</div><div class="decision-value {execution_tone}">{escape(str(analysis['execution']))}</div>{amount}</div>
        </div></div>""",
        unsafe_allow_html=True,
    )


def render_reasoning(analysis: dict) -> None:
    cards = [
        ("Bull case", analysis.get("bull_case"), "bull"),
        ("Bear case", analysis.get("bear_case"), "bear"),
        ("Final rationale", analysis.get("final_rationale"), "final"),
    ]
    body = "".join(
        f'<div class="rationale-card {tone}"><div class="rationale-label">{label}</div><div class="rationale-text">{escape(str(text or "Not available yet."))}</div></div>'
        for label, text, tone in cards
    )
    st.markdown(
        f'<div class="reasoning-grid">{body}</div>',
        unsafe_allow_html=True,
    )


def render_trades(trades: list[dict], symbol_filter: str | None = None) -> None:
    if symbol_filter and symbol_filter != "All symbols":
        trades = [trade for trade in trades if trade.get("symbol") == symbol_filter]
    trades = list(reversed(trades[-20:]))
    st.markdown('<div class="panel-title">RECENT TRADES</div>', unsafe_allow_html=True)
    if not trades:
        st.markdown('<div class="empty-state">No trades yet.</div>', unsafe_allow_html=True)
        return
    rows = []
    for trade in trades:
        pnl = as_float(trade.get("realized_pnl"))
        action = escape(str(trade.get("action", "—")))
        rows.append(f"""<tr><td>{format_time(trade.get('timestamp'))}</td><td>{escape(str(trade.get('symbol', '—')))}</td><td><span class="action-pill {action}">{action}</span></td><td>{format_currency(trade.get('total_value'))}</td><td>{format_currency(trade.get('execution_price'))}</td><td>{format_currency(trade.get('fees'), 4)}</td><td class="{_tone(pnl)}">{format_currency(pnl, 4, sign=True)}</td><td>{escape(str(trade.get('reason', '—')).replace('_', ' ').title())}</td></tr>""")
    st.markdown(f'<div class="ta-panel" style="overflow-x:auto"><table class="trade-table"><thead><tr><th>TIME</th><th>ASSET</th><th>ACTION</th><th>VALUE</th><th>PRICE</th><th>FEE</th><th>PNL</th><th>REASON</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>', unsafe_allow_html=True)


def render_performance(portfolio: dict, stats: dict) -> None:
    values = [
        ("Total trades", str(stats["total_trades"]), ""),
        ("Winning", str(stats["winning_trades"]), "positive"),
        ("Losing", str(stats["losing_trades"]), "negative"),
        ("Win rate", format_percent(stats["win_rate"]) if stats["win_rate"] is not None else "Not enough data", ""),
        ("Realized PnL", format_currency(portfolio["realized_pnl"], sign=True), _tone(portfolio["realized_pnl"])),
        ("Unrealized PnL", format_currency(portfolio["unrealized_pnl"], sign=True), _tone(portfolio["unrealized_pnl"])),
        ("Fees paid", format_currency(stats["fees_paid"], 4), ""),
        ("Best trade", format_currency(stats["best_trade"], 4, sign=True) if stats["best_trade"] is not None else "Not enough data", "positive"),
        ("Worst trade", format_currency(stats["worst_trade"], 4, sign=True) if stats["worst_trade"] is not None else "Not enough data", "negative"),
        ("Current drawdown", format_percent(portfolio["current_drawdown_percent"]), "negative"),
        ("Maximum drawdown", format_percent(portfolio["max_drawdown_percent"]), "negative"),
    ]
    body = "".join(f'<div class="mini-stat"><div class="detail-label">{label}</div><div class="mini-value {tone}">{value}</div></div>' for label, value, tone in values)
    st.markdown(f'<div class="ta-panel"><div class="panel-title">PERFORMANCE</div><div class="performance-grid">{body}</div></div>', unsafe_allow_html=True)


def render_risk(config) -> None:
    rules = [
        ("Max position", format_percent(config.max_position_percent)),
        ("Max exposure", format_percent(config.max_total_exposure_percent)),
        ("Max drawdown", format_percent(config.max_drawdown_percent)),
        ("Daily loss", format_percent(config.max_daily_loss_percent)),
        ("Leverage", "OFF" if not config.allow_leverage else "ON"),
        ("Negative balance", "BLOCKED" if not config.allow_negative_balance else "ALLOWED"),
        ("Stop loss", format_percent(config.stop_loss_percent)),
        ("Take profit", format_percent(config.take_profit_percent)),
        ("Trailing stop", f"{format_percent(config.trailing_stop_percent)} · {'ON' if config.trailing_stop_enabled else 'OFF'}"),
    ]
    body = "".join(f'<div class="mini-stat"><div class="detail-label">{label}</div><div class="mini-value">{value}</div></div>' for label, value in rules)
    st.markdown(f'<div class="ta-panel"><div class="panel-title">ACTIVE RISK RULES</div><div class="risk-grid">{body}</div></div>', unsafe_allow_html=True)
