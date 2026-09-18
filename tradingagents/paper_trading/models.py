"""Validated data contracts for paper orders, risk decisions, and portfolio state."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

ZERO = Decimal("0")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PaperAction(str, Enum):
    BUY = "BUY"
    INCREASE = "INCREASE"
    HOLD = "HOLD"
    REDUCE = "REDUCE"
    CLOSE = "CLOSE"


def parse_paper_action(value: PaperAction | str) -> PaperAction:
    if isinstance(value, PaperAction):
        return value
    return PaperAction(str(value).strip().upper())


DECISION_ACTION_MAP = {
    "Buy": PaperAction.BUY,
    "Overweight": PaperAction.INCREASE,
    "Hold": PaperAction.HOLD,
    "Underweight": PaperAction.REDUCE,
    "Sell": PaperAction.CLOSE,
}


def normalize_decision(rating: str) -> PaperAction:
    """Map the Portfolio Manager's five tiers onto the five lifecycle actions."""
    try:
        return DECISION_ACTION_MAP[rating]
    except KeyError as exc:
        raise ValueError("INVALID_DECISION") from exc


class MoneyModel(BaseModel):
    model_config = ConfigDict(validate_assignment=True)


class PaperOrderRequest(MoneyModel):
    """Explicit, non-executable intent derived from an LLM decision."""

    symbol: str = Field(min_length=1)
    action: PaperAction | str
    requested_allocation: Decimal
    reference_price: Decimal
    timestamp: str = Field(default_factory=utc_now_iso)
    source_decision: str
    reason: str = "PORTFOLIO_MANAGER"

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        value = value.strip().upper()
        if not value:
            raise ValueError("symbol must not be empty")
        return value


class RiskDecision(MoneyModel):
    approved: bool
    reason: str
    approved_amount: Decimal = ZERO
    risk_metrics: dict[str, Any] = Field(default_factory=dict)


class Position(MoneyModel):
    symbol: str
    quantity: Decimal
    entry_price: Decimal
    current_price: Decimal
    invested_amount: Decimal
    unrealized_pnl: Decimal = ZERO
    opened_at: str = Field(default_factory=utc_now_iso)
    last_price_update: str | None = None
    price_stale: bool = False
    stop_loss_price: Decimal | None = None
    take_profit_price: Decimal | None = None
    highest_price_since_entry: Decimal | None = None

    @property
    def market_value(self) -> Decimal:
        return self.quantity * self.current_price


class TradeRecord(MoneyModel):
    symbol: str
    action: PaperAction
    quantity: Decimal
    requested_price: Decimal
    execution_price: Decimal
    fees: Decimal
    slippage: Decimal
    total_value: Decimal
    timestamp: str
    balance_before: Decimal
    balance_after: Decimal
    realized_pnl: Decimal = ZERO
    reason: str = "PORTFOLIO_MANAGER"


class ExecutionResult(MoneyModel):
    executed: bool
    status: str
    reason: str = ""
    trade: TradeRecord | None = None


class MarketPrice(MoneyModel):
    symbol: str
    price_eur: Decimal
    source_price: Decimal
    source_currency: str
    fx_rate_to_eur: Decimal = Decimal("1")
    as_of: str


class EquitySnapshot(MoneyModel):
    timestamp: str
    cash: Decimal
    positions_value: Decimal
    total_equity: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_exposure: Decimal
    drawdown_percent: Decimal
