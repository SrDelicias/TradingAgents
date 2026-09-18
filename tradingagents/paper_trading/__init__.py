"""Local paper-trading simulator; never sends orders to a real venue."""

from .broker import PaperBroker
from .config import PaperTradingConfig
from .models import (
    EquitySnapshot,
    ExecutionResult,
    PaperAction,
    PaperOrderRequest,
    Position,
    RiskDecision,
    TradeRecord,
    normalize_decision,
)
from .node import PaperTradingNode, create_paper_trading_node
from .portfolio import VirtualPortfolio
from .risk_engine import HardRiskEngine

__all__ = [
    "ExecutionResult",
    "EquitySnapshot",
    "HardRiskEngine",
    "PaperAction",
    "PaperBroker",
    "PaperOrderRequest",
    "PaperTradingConfig",
    "PaperTradingNode",
    "Position",
    "RiskDecision",
    "TradeRecord",
    "VirtualPortfolio",
    "create_paper_trading_node",
    "normalize_decision",
]
