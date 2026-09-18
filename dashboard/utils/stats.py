"""Deterministic trade-performance calculations."""

from __future__ import annotations


def trade_statistics(trades: list[dict]) -> dict:
    exits = [trade for trade in trades if str(trade.get("action")) in {"REDUCE", "CLOSE"}]
    pnls = [float(trade.get("realized_pnl", 0)) for trade in exits]
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value < 0]
    return {
        "total_trades": len(trades),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate": len(wins) / len(pnls) if pnls else None,
        "fees_paid": sum(float(trade.get("fees", 0)) for trade in trades),
        "best_trade": max(pnls) if pnls else None,
        "worst_trade": min(pnls) if pnls else None,
        "enough_data": bool(pnls),
    }
