"""Read-only adapters from backend JSON/JSONL state to dashboard view models."""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path

from .formatting import parse_timestamp

EMPTY_PORTFOLIO = {
    "initial_balance": 50.0,
    "cash_balance": 50.0,
    "positions_value": 0.0,
    "total_equity": 50.0,
    "realized_pnl": 0.0,
    "unrealized_pnl": 0.0,
    "total_pnl": 0.0,
    "return_percent": 0.0,
    "total_exposure": 0.0,
    "high_water_mark": 50.0,
    "current_drawdown_percent": 0.0,
    "max_drawdown_percent": 0.0,
    "positions": {},
    "trade_history": [],
}

PIPELINE_STAGES = [
    ("Market Analyst", {"Market Analyst"}),
    ("News Analyst", {"News Analyst"}),
    ("Sentiment Analyst", {"Sentiment Analyst"}),
    ("Fundamentals", {"Fundamentals Analyst"}),
    ("Bull ↔ Bear", {"Bull Researcher", "Bear Researcher"}),
    ("Research Manager", {"Research Manager"}),
    ("Trader", {"Trader"}),
    ("Risk Debate", {"Aggressive Analyst", "Neutral Analyst", "Conservative Analyst"}),
    ("Portfolio Manager", {"Portfolio Manager"}),
    ("Hard Risk Engine", {"Hard Risk Engine"}),
    ("Paper Broker", {"Paper Broker"}),
]


def _read_json(path: str | Path, fallback):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return fallback


def load_portfolio(path: str | Path) -> dict:
    raw = _read_json(path, {})
    if not isinstance(raw, dict):
        raw = {}
    result = {**EMPTY_PORTFOLIO, **raw}
    result["positions"] = raw.get("positions", {}) if isinstance(raw.get("positions", {}), dict) else {}
    result["trade_history"] = raw.get("trade_history", []) if isinstance(raw.get("trade_history", []), list) else []
    invested = sum(
        float(position.get("quantity", 0)) * float(position.get("current_price", 0))
        for position in result["positions"].values()
    )
    result["positions_value"] = float(raw.get("positions_value", invested))
    result["total_exposure"] = float(raw.get("total_exposure", invested))
    result["total_equity"] = float(raw.get("total_equity", float(result["cash_balance"]) + invested))
    result["total_pnl"] = float(raw.get("total_pnl", result["total_equity"] - float(result["initial_balance"])))
    result["return_percent"] = float(
        raw.get(
            "return_percent",
            result["total_pnl"] / float(result["initial_balance"])
            if float(result["initial_balance"])
            else 0,
        )
    )
    return result


def load_equity_history(path: str | Path) -> list[dict]:
    raw = _read_json(path, [])
    if not isinstance(raw, list):
        return []
    valid = [item for item in raw if isinstance(item, dict) and parse_timestamp(item.get("timestamp"))]
    return sorted(valid, key=lambda item: parse_timestamp(item["timestamp"]))


def load_events(path: str | Path, limit: int = 500) -> list[dict]:
    events: deque[dict] = deque(maxlen=limit)
    try:
        with Path(path).open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict) and parse_timestamp(event.get("timestamp")):
                    event.setdefault("metadata", {})
                    events.append(event)
    except OSError:
        return []
    return list(events)


def pipeline_status(events: list[dict]) -> list[dict]:
    rows = []
    for label, aliases in PIPELINE_STAGES:
        relevant = [event for event in events if event.get("stage") in aliases]
        status = str(relevant[-1].get("status", "PENDING")).upper() if relevant else "PENDING"
        rows.append({"stage": label, "status": status})
    return rows


def current_analysis(events: list[dict]) -> dict:
    starts = [event for event in events if event.get("type") == "ANALYSIS_STARTED"]
    if not starts:
        return {
            "system_status": "IDLE",
            "symbol": "—",
            "price": None,
            "stage": "Waiting for first analysis...",
            "started": None,
            "completed": None,
            "decision": "Waiting...",
            "normalized": "Waiting...",
            "risk": "Waiting...",
            "execution": "Waiting...",
            "risk_reason": "",
            "bull_case": "Waiting for Bull Researcher...",
            "bear_case": "Waiting for Bear Researcher...",
            "final_rationale": "Waiting for Portfolio Manager...",
        }
    started = starts[-1]
    started_at = parse_timestamp(started.get("timestamp"))
    relevant = [
        event
        for event in events
        if parse_timestamp(event.get("timestamp"))
        and parse_timestamp(event["timestamp"]) >= started_at
    ]
    completed = [event for event in relevant if event.get("type") == "ANALYSIS_COMPLETED"]
    errors = [event for event in relevant if event.get("status") == "ERROR"]
    status = "ERROR" if errors else ("IDLE" if completed else "ANALYSING")
    pipeline_aliases = set().union(*(aliases for _, aliases in PIPELINE_STAGES))
    stage_events = [event for event in relevant if event.get("stage") in pipeline_aliases]
    stage = stage_events[-1].get("stage") if stage_events else "Starting"
    price_events = [event for event in relevant if event.get("type") == "MARKET_PRICE_UPDATED"]
    decisions = [event for event in relevant if event.get("type") == "PORTFOLIO_DECISION"]
    normalized = [event for event in relevant if event.get("type") == "DECISION_NORMALIZED"]
    risks = [event for event in relevant if event.get("type") in {"RISK_APPROVED", "RISK_REJECTED"}]
    executions = [event for event in relevant if event.get("type") == "ORDER_EXECUTED"]
    summaries = {
        event.get("stage"): (event.get("metadata") or {}).get("summary")
        for event in relevant
        if (event.get("metadata") or {}).get("summary")
    }
    if completed:
        stage = "Completed"
    return {
        "system_status": status,
        "symbol": started.get("symbol") or "—",
        "price": (price_events[-1].get("metadata") or {}).get("price") if price_events else None,
        "stage": stage,
        "started": started.get("timestamp"),
        "completed": completed[-1].get("timestamp") if completed else None,
        "decision": (decisions[-1].get("metadata") or {}).get("decision", "Waiting...") if decisions else "Waiting...",
        "normalized": (normalized[-1].get("metadata") or {}).get("normalized_action", "Waiting...") if normalized else "Waiting...",
        "risk": ("APPROVED" if risks[-1].get("type") == "RISK_APPROVED" else "REJECTED") if risks else "Waiting...",
        "risk_reason": (risks[-1].get("metadata") or {}).get("reason", "") if risks else "",
        "execution": "EXECUTED" if executions else ("NOT EXECUTED" if risks and risks[-1].get("type") == "RISK_REJECTED" else "Waiting..."),
        "execution_value": (executions[-1].get("metadata") or {}).get("total_value") if executions else None,
        "bull_case": summaries.get("Bull Researcher", "Waiting for Bull Researcher..."),
        "bear_case": summaries.get("Bear Researcher", "Waiting for Bear Researcher..."),
        "final_rationale": summaries.get("Portfolio Manager", "Waiting for Portfolio Manager..."),
    }


def filter_by_symbol(items: list[dict], symbol: str | None) -> list[dict]:
    if not symbol or symbol == "All symbols":
        return items
    return [item for item in items if item.get("symbol") == symbol]
