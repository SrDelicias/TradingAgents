"""Append-only structured events with no dependency on Streamlit."""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_WRITE_LOCK = threading.Lock()
_ONCE_KEYS: set[tuple[str, str]] = set()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _brief_agent_summary(stage: str, result: Any, limit: int = 260) -> str:
    """Extract a short display excerpt without persisting full LLM output."""
    if not isinstance(result, dict):
        return ""
    text = ""
    if stage in {"Bull Researcher", "Bear Researcher"}:
        debate = result.get("investment_debate_state") or {}
        text = str(debate.get("current_response") or "")
    elif stage == "Portfolio Manager":
        text = str(result.get("final_trade_decision") or "")
    if not text:
        return ""
    cleaned = re.sub(r"[#*_`>|]+", " ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) <= limit:
        return cleaned
    shortened = cleaned[: limit + 1].rsplit(" ", 1)[0]
    return shortened.rstrip(".,;:") + "…"


class EventLog:
    """Best-effort JSONL event sink; observability must never break trading."""

    def __init__(self, path: str | Path | None) -> None:
        self.path = Path(path) if path else None

    def emit(
        self,
        event_type: str,
        *,
        stage: str = "system",
        symbol: str = "",
        status: str = "COMPLETED",
        message: str = "",
        metadata: dict[str, Any] | None = None,
        timestamp: str | None = None,
    ) -> bool:
        if self.path is None:
            return False
        event = {
            "timestamp": timestamp or utc_now_iso(),
            "type": event_type,
            "stage": stage,
            "symbol": symbol.upper(),
            "status": status.upper(),
            "message": message,
            "metadata": metadata or {},
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            encoded = json.dumps(event, ensure_ascii=False, default=str)
            with _WRITE_LOCK, self.path.open("a", encoding="utf-8") as handle:
                handle.write(encoded + "\n")
            return True
        except OSError:
            return False

    def emit_once(self, event_type: str, **kwargs) -> bool:
        """Emit one event type per destination for this process."""
        if self.path is None:
            return False
        key = (str(self.path.resolve()), event_type)
        with _WRITE_LOCK:
            if key in _ONCE_KEYS:
                return False
            _ONCE_KEYS.add(key)
        return self.emit(event_type, **kwargs)


def instrument_node(
    node: Callable,
    *,
    stage: str,
    event_log: EventLog,
    analysis_start: bool = False,
) -> Callable:
    """Wrap a LangGraph node with concise start/completion/error events."""

    def monitored(state):
        symbol = str(state.get("company_of_interest", "")).upper()
        if analysis_start:
            event_log.emit_once(
                "SYSTEM_STARTED",
                stage="system",
                status="COMPLETED",
                message="TradingAgents initialized in paper-trading mode",
                metadata={"paper_trading": True, "real_money": False},
            )
            event_log.emit(
                "ANALYSIS_STARTED",
                stage="analysis",
                symbol=symbol,
                status="RUNNING",
                message=f"{symbol} analysis started",
            )
        event_log.emit(
            "AGENT_STARTED",
            stage=stage,
            symbol=symbol,
            status="RUNNING",
            message=f"{stage} started",
        )
        try:
            result = node(state)
        except Exception as exc:
            event_log.emit(
                "AGENT_ERROR",
                stage=stage,
                symbol=symbol,
                status="ERROR",
                message=f"{stage} failed",
                metadata={"error": type(exc).__name__},
            )
            raise
        summary = _brief_agent_summary(stage, result)
        event_log.emit(
            "AGENT_COMPLETED",
            stage=stage,
            symbol=symbol,
            status="COMPLETED",
            message=f"{stage} completed",
            metadata={"summary": summary} if summary else {},
        )
        return result

    return monitored
