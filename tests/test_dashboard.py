"""Pure tests for dashboard data adapters, events, formatting, and demo mode."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from dashboard.demo_data import build_demo_data
from dashboard.utils.data import (
    current_analysis,
    load_equity_history,
    load_events,
    load_portfolio,
    pipeline_status,
)
from dashboard.utils.formatting import (
    format_currency,
    format_elapsed,
    format_percent,
    format_quantity,
    format_time,
)
from dashboard.utils.stats import trade_statistics
from tradingagents.monitoring import EventLog, instrument_node


@pytest.mark.unit
def test_missing_portfolio_has_safe_empty_state(tmp_path):
    portfolio = load_portfolio(tmp_path / "missing.json")
    assert portfolio["total_equity"] == 50
    assert portfolio["cash_balance"] == 50
    assert portfolio["positions"] == {}
    assert portfolio["trade_history"] == []


@pytest.mark.unit
def test_portfolio_reader_derives_live_values(tmp_path):
    path = tmp_path / "portfolio.json"
    path.write_text(
        json.dumps(
            {
                "initial_balance": "50",
                "cash_balance": "45",
                "positions": {
                    "BTC-USD": {"quantity": "0.05", "current_price": "100"}
                },
            }
        ),
        encoding="utf-8",
    )
    portfolio = load_portfolio(path)
    assert portfolio["positions_value"] == 5
    assert portfolio["total_equity"] == 50
    assert portfolio["total_pnl"] == 0


@pytest.mark.unit
def test_equity_reader_orders_and_ignores_invalid_rows(tmp_path):
    path = tmp_path / "equity.json"
    path.write_text(
        json.dumps(
            [
                {"timestamp": "2026-01-02T00:00:00+00:00", "total_equity": 51},
                {"bad": "row"},
                {"timestamp": "2026-01-01T00:00:00+00:00", "total_equity": 50},
            ]
        ),
        encoding="utf-8",
    )
    history = load_equity_history(path)
    assert [row["total_equity"] for row in history] == [50, 51]


@pytest.mark.unit
def test_event_log_and_parser_skip_malformed_lines(tmp_path):
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    assert log.emit(
        "ANALYSIS_STARTED",
        stage="analysis",
        symbol="btc-usd",
        status="RUNNING",
        message="BTC analysis started",
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write("not json\n")
    events = load_events(path)
    assert len(events) == 1
    assert events[0]["symbol"] == "BTC-USD"
    assert events[0]["type"] == "ANALYSIS_STARTED"


@pytest.mark.unit
def test_instrumented_agent_emits_concise_lifecycle_events(tmp_path):
    path = tmp_path / "events.jsonl"
    wrapped = instrument_node(
        lambda state: {"ok": state["company_of_interest"]},
        stage="Market Analyst",
        event_log=EventLog(path),
        analysis_start=True,
    )
    assert wrapped({"company_of_interest": "BTC-USD"}) == {"ok": "BTC-USD"}
    assert wrapped({"company_of_interest": "ETH-USD"}) == {"ok": "ETH-USD"}
    events = load_events(path)
    assert sum(event["type"] == "SYSTEM_STARTED" for event in events) == 1
    assert sum(event["type"] == "ANALYSIS_STARTED" for event in events) == 2
    assert events[-1]["type"] == "AGENT_COMPLETED"


@pytest.mark.unit
def test_instrumented_debate_agent_emits_only_a_brief_summary(tmp_path):
    path = tmp_path / "events.jsonl"
    full_response = "Bullish evidence " * 80
    wrapped = instrument_node(
        lambda _state: {
            "investment_debate_state": {"current_response": full_response}
        },
        stage="Bull Researcher",
        event_log=EventLog(path),
    )
    wrapped({"company_of_interest": "BTC-USD"})
    completed = load_events(path)[-1]
    summary = completed["metadata"]["summary"]
    assert len(summary) <= 261
    assert summary.endswith("…")
    assert summary != full_response


@pytest.mark.unit
def test_current_analysis_and_pipeline_from_events():
    demo = build_demo_data(datetime(2026, 1, 1, tzinfo=timezone.utc))
    analysis = current_analysis(demo["events"])
    stages = pipeline_status(demo["events"])
    assert analysis["symbol"] == "BTC-USD"
    assert analysis["decision"] == "Overweight"
    assert analysis["normalized"] == "INCREASE"
    assert analysis["risk"] == "APPROVED"
    assert analysis["execution"] == "EXECUTED"
    assert analysis["bull_case"]
    assert analysis["bear_case"]
    assert analysis["final_rationale"]
    assert next(row for row in stages if row["stage"] == "Market Analyst")["status"] == "COMPLETED"


@pytest.mark.unit
def test_empty_analysis_is_idle():
    analysis = current_analysis([])
    assert analysis["system_status"] == "IDLE"
    assert analysis["decision"] == "Waiting..."
    assert all(row["status"] == "PENDING" for row in pipeline_status([]))


@pytest.mark.unit
def test_trade_statistics_only_scores_exit_trades():
    trades = [
        {"action": "BUY", "realized_pnl": "-0.01", "fees": "0.01"},
        {"action": "REDUCE", "realized_pnl": "0.20", "fees": "0.01"},
        {"action": "CLOSE", "realized_pnl": "-0.10", "fees": "0.01"},
    ]
    stats = trade_statistics(trades)
    assert stats["total_trades"] == 3
    assert stats["winning_trades"] == 1
    assert stats["losing_trades"] == 1
    assert stats["win_rate"] == DecimalApprox("0.5")
    assert stats["fees_paid"] == DecimalApprox("0.03")
    assert stats["best_trade"] == DecimalApprox("0.20")
    assert stats["worst_trade"] == DecimalApprox("-0.10")


def DecimalApprox(value: str):
    return pytest.approx(float(value))


@pytest.mark.unit
def test_european_formatting():
    assert format_currency(66725.15) == "66.725,15 €"
    assert format_currency(0.43, sign=True) == "+0,43 €"
    assert format_percent(0.0086, sign=True) == "+0,86 %"
    assert format_quantity(0.00007493, "BTC-USD") == "0,00007493 BTC"
    assert format_time("2026-01-01T16:42:01+00:00") != "—"
    assert (
        format_elapsed(
            "2026-01-01T16:42:01+00:00",
            datetime(2026, 1, 1, 16, 42, 24, tzinfo=timezone.utc),
        )
        == "00:00:23"
    )


@pytest.mark.unit
def test_demo_data_is_complete_and_in_memory(tmp_path):
    before = list(tmp_path.iterdir())
    demo = build_demo_data(datetime(2026, 1, 1, tzinfo=timezone.utc))
    after = list(tmp_path.iterdir())
    assert before == after
    assert len(demo["portfolio"]["positions"]) >= 2
    assert demo["equity_history"]
    assert demo["events"]
    assert demo["portfolio"]["trade_history"]
