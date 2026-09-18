"""Configuration for the local, simulated paper-trading layer."""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

INITIAL_BALANCE = Decimal("50.00")
ALLOW_LEVERAGE = False
ALLOW_NEGATIVE_BALANCE = False

MAX_POSITION_PERCENT = Decimal("0.10")
MAX_TOTAL_EXPOSURE_PERCENT = Decimal("0.50")
MAX_DAILY_LOSS_PERCENT = Decimal("0.03")
MAX_OPEN_POSITIONS = 5
MIN_ORDER_VALUE_EUR = Decimal("1.00")
REDUCE_POSITION_PERCENT = Decimal("0.50")
BUY_ALLOCATION_PERCENT = Decimal("0.05")
INCREASE_ALLOCATION_PERCENT = Decimal("0.05")

STOP_LOSS_PERCENT = Decimal("0.03")
TAKE_PROFIT_PERCENT = Decimal("0.06")
TRAILING_STOP_ENABLED = False
TRAILING_STOP_PERCENT = Decimal("0.03")
MAX_DRAWDOWN_PERCENT = Decimal("0.10")

TRADING_FEE_PERCENT = Decimal("0.001")
SLIPPAGE_PERCENT = Decimal("0.0005")
SPREAD_PERCENT = Decimal("0.0005")

DEFAULT_PORTFOLIO_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "paper_portfolio.json"
)
DEFAULT_EQUITY_HISTORY_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "equity_history.json"
)
DEFAULT_EVENTS_PATH = Path(__file__).resolve().parents[2] / "data" / "events.jsonl"


@dataclass(frozen=True)
class PaperTradingConfig:
    """All hard limits used by the deterministic simulator."""

    enabled: bool = True
    initial_balance: Decimal = INITIAL_BALANCE
    allow_leverage: bool = ALLOW_LEVERAGE
    allow_negative_balance: bool = ALLOW_NEGATIVE_BALANCE
    max_position_percent: Decimal = MAX_POSITION_PERCENT
    max_total_exposure_percent: Decimal = MAX_TOTAL_EXPOSURE_PERCENT
    max_daily_loss_percent: Decimal = MAX_DAILY_LOSS_PERCENT
    max_open_positions: int = MAX_OPEN_POSITIONS
    min_order_value_eur: Decimal = MIN_ORDER_VALUE_EUR
    reduce_position_percent: Decimal = REDUCE_POSITION_PERCENT
    buy_allocation_percent: Decimal = BUY_ALLOCATION_PERCENT
    increase_allocation_percent: Decimal = INCREASE_ALLOCATION_PERCENT
    stop_loss_percent: Decimal = STOP_LOSS_PERCENT
    take_profit_percent: Decimal = TAKE_PROFIT_PERCENT
    trailing_stop_enabled: bool = TRAILING_STOP_ENABLED
    trailing_stop_percent: Decimal = TRAILING_STOP_PERCENT
    max_drawdown_percent: Decimal = MAX_DRAWDOWN_PERCENT
    trading_fee_percent: Decimal = TRADING_FEE_PERCENT
    slippage_percent: Decimal = SLIPPAGE_PERCENT
    spread_percent: Decimal = SPREAD_PERCENT
    portfolio_path: Path = DEFAULT_PORTFOLIO_PATH
    equity_history_path: Path = DEFAULT_EQUITY_HISTORY_PATH
    events_path: Path = DEFAULT_EVENTS_PATH

    @classmethod
    def from_mapping(cls, values: dict | None = None) -> PaperTradingConfig:
        """Build config from the graph config plus an optional path env override."""
        values = values or {}
        configured_path = values.get("paper_portfolio_path") or os.getenv(
            "TRADINGAGENTS_PAPER_PORTFOLIO_PATH"
        )
        history_path = values.get("paper_equity_history_path") or os.getenv(
            "TRADINGAGENTS_PAPER_EQUITY_HISTORY_PATH"
        )
        events_path = values.get("paper_events_path") or os.getenv(
            "TRADINGAGENTS_PAPER_EVENTS_PATH"
        )
        return cls(
            enabled=bool(values.get("paper_trading_enabled", True)),
            portfolio_path=(
                Path(configured_path).expanduser()
                if configured_path
                else DEFAULT_PORTFOLIO_PATH
            ),
            equity_history_path=(
                Path(history_path).expanduser()
                if history_path
                else DEFAULT_EQUITY_HISTORY_PATH
            ),
            events_path=(
                Path(events_path).expanduser()
                if events_path
                else (
                    Path(configured_path).expanduser().with_name("events.jsonl")
                    if configured_path
                    else DEFAULT_EVENTS_PATH
                )
            ),
        )
