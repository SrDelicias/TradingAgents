"""In-memory dashboard showcase. It never writes backend state."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def build_demo_data(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)

    def stamp(seconds_ago: int) -> str:
        return (now - timedelta(seconds=seconds_ago)).isoformat()

    positions = {
        "BTC-USD": {
            "symbol": "BTC-USD",
            "quantity": "0.00007648",
            "entry_price": "66400",
            "current_price": "67994",
            "invested_amount": "5.078272",
            "unrealized_pnl": "0.121902",
            "market_value": 5.200174,
            "return_percent": 0.0240,
            "stop_loss_price": "64408",
            "take_profit_price": "70384",
            "price_stale": False,
        },
        "ETH-USD": {
            "symbol": "ETH-USD",
            "quantity": "0.00143000",
            "entry_price": "3420",
            "current_price": "3392.64",
            "invested_amount": "4.890600",
            "unrealized_pnl": "-0.039125",
            "market_value": 4.851475,
            "return_percent": -0.0080,
            "stop_loss_price": "3317.40",
            "take_profit_price": "3625.20",
            "price_stale": False,
        },
    }
    trades = [
        {
            "timestamp": stamp(8600),
            "symbol": "BTC-USD",
            "action": "BUY",
            "total_value": "2.50",
            "execution_price": "66400",
            "quantity": "0.00003765",
            "fees": "0.0025",
            "realized_pnl": "-0.0025",
            "reason": "PORTFOLIO_MANAGER",
        },
        {
            "timestamp": stamp(4200),
            "symbol": "ETH-USD",
            "action": "REDUCE",
            "total_value": "2.41",
            "execution_price": "3422",
            "quantity": "0.00070427",
            "fees": "0.0024",
            "realized_pnl": "0.14",
            "reason": "PORTFOLIO_MANAGER",
        },
        {
            "timestamp": stamp(15),
            "symbol": "BTC-USD",
            "action": "INCREASE",
            "total_value": "2.50",
            "execution_price": "67725",
            "quantity": "0.00003691",
            "fees": "0.0025",
            "realized_pnl": "-0.0025",
            "reason": "PORTFOLIO_MANAGER",
        },
    ]
    equity = []
    values = [50.0, 49.97, 50.08, 50.02, 50.21, 50.29, 50.43]
    for index, value in enumerate(values):
        equity.append(
            {
                "timestamp": (now - timedelta(hours=6 - index)).isoformat(),
                "cash": 40.38,
                "positions_value": value - 40.38,
                "total_equity": value,
                "realized_pnl": 0.13,
                "unrealized_pnl": value - 50.13,
                "total_exposure": value - 40.38,
                "drawdown_percent": max(0, (50.43 - value) / 50.43),
            }
        )

    def event(seconds, event_type, stage, status, message, metadata=None):
        return {
            "timestamp": stamp(seconds),
            "type": event_type,
            "stage": stage,
            "symbol": "BTC-USD",
            "status": status,
            "message": message,
            "metadata": metadata or {},
        }

    events = [
        event(38, "SYSTEM_STARTED", "system", "COMPLETED", "TradingAgents initialized"),
        event(31, "ANALYSIS_STARTED", "analysis", "RUNNING", "BTC-USD analysis started"),
        event(29, "AGENT_STARTED", "Market Analyst", "RUNNING", "Market Analyst started"),
        event(27, "AGENT_COMPLETED", "Market Analyst", "COMPLETED", "Market Analyst completed"),
        event(26, "AGENT_COMPLETED", "News Analyst", "COMPLETED", "News Analyst completed"),
        event(25, "AGENT_COMPLETED", "Sentiment Analyst", "COMPLETED", "Sentiment Analyst completed"),
        event(23, "AGENT_COMPLETED", "Fundamentals Analyst", "COMPLETED", "Fundamentals completed"),
        event(
            20,
            "AGENT_COMPLETED",
            "Bull Researcher",
            "COMPLETED",
            "Bull thesis completed",
            {
                "summary": "Momentum remains constructive, liquidity is healthy and the latest price action supports a measured increase in exposure."
            },
        ),
        event(
            18,
            "AGENT_COMPLETED",
            "Bear Researcher",
            "COMPLETED",
            "Bear thesis completed",
            {
                "summary": "Short-term volatility and concentration risk argue for keeping the position small and respecting the hard allocation cap."
            },
        ),
        event(16, "AGENT_COMPLETED", "Research Manager", "COMPLETED", "Research plan completed"),
        event(14, "AGENT_COMPLETED", "Trader", "COMPLETED", "Trader completed"),
        event(12, "AGENT_COMPLETED", "Aggressive Analyst", "COMPLETED", "Risk debate completed"),
        event(9, "MARKET_PRICE_UPDATED", "Mark to Market", "COMPLETED", "BTC-USD price → 67994 EUR", {"price": "67994"}),
        event(
            8,
            "AGENT_COMPLETED",
            "Portfolio Manager",
            "COMPLETED",
            "Portfolio Manager completed",
            {
                "summary": "The bullish setup outweighs the near-term downside, but the portfolio should add only 2.50 € to remain close to the configured exposure limit."
            },
        ),
        event(8, "PORTFOLIO_DECISION", "Portfolio Manager", "COMPLETED", "Portfolio Manager → OVERWEIGHT", {"decision": "Overweight"}),
        event(7, "DECISION_NORMALIZED", "Decision Normalizer", "COMPLETED", "Decision normalized → INCREASE", {"normalized_action": "INCREASE"}),
        event(6, "RISK_APPROVED", "Hard Risk Engine", "COMPLETED", "Hard Risk Engine → APPROVED", {"reason": "APPROVED"}),
        event(5, "ORDER_EXECUTED", "Paper Broker", "COMPLETED", "INCREASE BTC-USD 2.50 EUR", {"action": "INCREASE", "total_value": "2.50"}),
        event(3, "PORTFOLIO_UPDATED", "Virtual Portfolio", "COMPLETED", "Portfolio equity → 50.43 EUR"),
    ]
    portfolio = {
        "initial_balance": 50.0,
        "cash_balance": 40.38,
        "positions_value": 10.051649,
        "total_equity": 50.431649,
        "realized_pnl": 0.135,
        "unrealized_pnl": 0.082777,
        "total_pnl": 0.431649,
        "return_percent": 0.00863298,
        "total_exposure": 10.051649,
        "high_water_mark": 50.45,
        "current_drawdown_percent": 0.0003637,
        "max_drawdown_percent": 0.0124,
        "positions": positions,
        "trade_history": trades,
    }
    return {"portfolio": portfolio, "equity_history": equity, "events": events}
