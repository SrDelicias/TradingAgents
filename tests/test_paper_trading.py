"""Unit tests for the deterministic, cash-only paper-trading layer."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from tradingagents.graph.conditional_logic import ConditionalLogic
from tradingagents.graph.setup import GraphSetup
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.paper_trading import (
    HardRiskEngine,
    PaperAction,
    PaperBroker,
    PaperOrderRequest,
    PaperTradingConfig,
    RiskDecision,
    VirtualPortfolio,
    normalize_decision,
)
from tradingagents.paper_trading.models import MarketPrice, Position, TradeRecord
from tradingagents.paper_trading.node import PaperTradingNode
from tradingagents.paper_trading.price_service import PriceUnavailableError

NOW = "2026-09-17T10:00:00+00:00"


@pytest.fixture()
def paper_config(tmp_path):
    return PaperTradingConfig(
        portfolio_path=tmp_path / "paper_portfolio.json",
        equity_history_path=tmp_path / "equity_history.json",
        events_path=tmp_path / "events.jsonl",
    )


@pytest.fixture()
def portfolio(paper_config):
    return VirtualPortfolio(paper_config.portfolio_path)


def order(
    action=PaperAction.BUY,
    amount="5",
    price="100",
    symbol="TEST",
    timestamp=NOW,
):
    return PaperOrderRequest(
        symbol=symbol,
        action=action,
        requested_allocation=Decimal(amount),
        reference_price=Decimal(price),
        timestamp=timestamp,
        source_decision=f"Rating: {action}",
    )


def execute_buy(portfolio, paper_config, amount="5", price="100", symbol="TEST"):
    request = order(amount=amount, price=price, symbol=symbol)
    risk = HardRiskEngine(paper_config).evaluate(request, portfolio)
    execution = PaperBroker(paper_config).execute(request, risk, portfolio)
    return request, risk, execution


@pytest.mark.unit
def test_new_portfolio_starts_with_50_eur_and_persists(portfolio, paper_config):
    assert portfolio.initial_balance == Decimal("50.00")
    assert portfolio.cash_balance == Decimal("50.00")
    assert portfolio.total_equity == Decimal("50.00")
    assert portfolio.positions == {}
    assert portfolio.trade_history == []
    assert paper_config.portfolio_path.exists()


@pytest.mark.unit
def test_decision_normalization_is_deterministic():
    assert normalize_decision("Buy") is PaperAction.BUY
    assert normalize_decision("Overweight") is PaperAction.INCREASE
    assert normalize_decision("Hold") is PaperAction.HOLD
    assert normalize_decision("Underweight") is PaperAction.REDUCE
    assert normalize_decision("Sell") is PaperAction.CLOSE


@pytest.mark.unit
def test_invalid_decision_is_rejected():
    with pytest.raises(ValueError, match="INVALID_DECISION"):
        normalize_decision("Strong Buy")


@pytest.mark.unit
def test_valid_buy_updates_cash_position_and_history(portfolio, paper_config):
    _, risk, execution = execute_buy(portfolio, paper_config)
    assert risk.approved
    assert execution.executed
    assert execution.status == "FILLED"
    assert portfolio.cash_balance == Decimal("44.995")
    assert portfolio.positions["TEST"].quantity > 0
    assert len(portfolio.trade_history) == 1
    assert portfolio.cash_balance >= 0


@pytest.mark.unit
def test_valid_sell_closes_position(portfolio, paper_config):
    execute_buy(portfolio, paper_config)
    position_value = portfolio.positions["TEST"].market_value
    sell = order(action=PaperAction.CLOSE, amount=str(position_value), price="100")
    risk = HardRiskEngine(paper_config).evaluate(sell, portfolio)
    execution = PaperBroker(paper_config).execute(sell, risk, portfolio)
    assert risk.approved
    assert execution.executed
    assert "TEST" not in portfolio.positions
    assert len(portfolio.trade_history) == 2
    assert portfolio.cash_balance >= 0


@pytest.mark.unit
def test_hold_never_creates_a_trade(portfolio, paper_config):
    request = order(action=PaperAction.HOLD, amount="0")
    risk = HardRiskEngine(paper_config).evaluate(request, portfolio)
    execution = PaperBroker(paper_config).execute(request, risk, portfolio)
    assert not risk.approved
    assert risk.reason == "HOLD"
    assert not execution.executed
    assert portfolio.trade_history == []


@pytest.mark.unit
def test_buy_above_cash_is_rejected(portfolio, paper_config):
    risk = HardRiskEngine(paper_config).evaluate(order(amount="60"), portfolio)
    assert not risk.approved
    assert risk.reason == "INSUFFICIENT_FUNDS"


@pytest.mark.unit
def test_buy_above_ten_percent_is_rejected(portfolio, paper_config):
    risk = HardRiskEngine(paper_config).evaluate(order(amount="6"), portfolio)
    assert not risk.approved
    assert risk.reason == "MAX_POSITION_SIZE"


@pytest.mark.unit
def test_broker_defence_prevents_negative_cash(portfolio, paper_config):
    forged_approval = RiskDecision(
        approved=True,
        reason="APPROVED",
        approved_amount=Decimal("100"),
    )
    result = PaperBroker(paper_config).execute(order(amount="100"), forged_approval, portfolio)
    assert not result.executed
    assert result.reason == "INSUFFICIENT_FUNDS"
    assert portfolio.cash_balance == Decimal("50.00")


@pytest.mark.unit
def test_sell_without_position_is_rejected(portfolio, paper_config):
    risk = HardRiskEngine(paper_config).evaluate(
        order(action=PaperAction.CLOSE, amount="1"), portfolio
    )
    assert not risk.approved
    assert risk.reason == "POSITION_NOT_FOUND"


@pytest.mark.unit
def test_sell_above_owned_quantity_is_rejected(portfolio, paper_config):
    execute_buy(portfolio, paper_config)
    risk = HardRiskEngine(paper_config).evaluate(
        order(action=PaperAction.CLOSE, amount="6"), portfolio
    )
    assert not risk.approved
    assert risk.reason == "INSUFFICIENT_POSITION"


@pytest.mark.unit
def test_fee_calculation(portfolio, paper_config):
    _, _, execution = execute_buy(portfolio, paper_config)
    assert execution.trade.fees == Decimal("0.005")
    assert portfolio.realized_pnl == Decimal("-0.005")


@pytest.mark.unit
def test_spread_and_slippage_make_buy_price_adverse(portfolio, paper_config):
    _, _, execution = execute_buy(portfolio, paper_config)
    assert execution.trade.execution_price == Decimal("100.1000")
    assert execution.trade.execution_price > execution.trade.requested_price
    assert execution.trade.slippage > 0


@pytest.mark.unit
def test_spread_and_slippage_make_sell_price_adverse(portfolio, paper_config):
    execute_buy(portfolio, paper_config)
    position_value = portfolio.positions["TEST"].market_value
    sell = order(action=PaperAction.CLOSE, amount=str(position_value))
    risk = HardRiskEngine(paper_config).evaluate(sell, portfolio)
    execution = PaperBroker(paper_config).execute(sell, risk, portfolio)
    assert execution.trade.execution_price == Decimal("99.9000")
    assert execution.trade.execution_price < execution.trade.requested_price


@pytest.mark.unit
def test_json_round_trip_recovers_portfolio(portfolio, paper_config):
    execute_buy(portfolio, paper_config)
    recovered = VirtualPortfolio.load(paper_config.portfolio_path)
    assert recovered.cash_balance == portfolio.cash_balance
    assert recovered.total_equity == portfolio.total_equity
    assert recovered.positions["TEST"].quantity == portfolio.positions["TEST"].quantity
    assert len(recovered.trade_history) == 1


@pytest.mark.unit
def test_daily_loss_limit_blocks_new_buys(portfolio, paper_config):
    portfolio.trade_history.append(
        TradeRecord(
            symbol="LOSS",
            action=PaperAction.CLOSE,
            quantity=Decimal("1"),
            requested_price=Decimal("10"),
            execution_price=Decimal("8"),
            fees=Decimal("0"),
            slippage=Decimal("0"),
            total_value=Decimal("8"),
            timestamp=NOW,
            balance_before=Decimal("50"),
            balance_after=Decimal("48"),
            realized_pnl=Decimal("-2"),
        )
    )
    risk = HardRiskEngine(paper_config).evaluate(order(amount="1"), portfolio)
    assert not risk.approved
    assert risk.reason == "MAX_DAILY_LOSS"


@pytest.mark.unit
def test_maximum_five_open_positions(portfolio, paper_config):
    for index in range(5):
        symbol = f"P{index}"
        portfolio.positions[symbol] = Position(
            symbol=symbol,
            quantity=Decimal("0.01"),
            entry_price=Decimal("100"),
            current_price=Decimal("100"),
            invested_amount=Decimal("1"),
            opened_at=NOW,
        )
    portfolio.refresh_metrics()
    risk = HardRiskEngine(paper_config).evaluate(
        order(amount="1", symbol="SIXTH"), portfolio
    )
    assert not risk.approved
    assert risk.reason == "MAX_OPEN_POSITIONS"


@pytest.mark.unit
def test_maximum_total_exposure(portfolio, paper_config):
    portfolio.positions["LARGE"] = Position(
        symbol="LARGE",
        quantity=Decimal("0.24"),
        entry_price=Decimal("100"),
        current_price=Decimal("100"),
        invested_amount=Decimal("24"),
        opened_at=NOW,
    )
    portfolio.cash_balance = Decimal("26")
    portfolio.refresh_metrics()
    risk = HardRiskEngine(paper_config).evaluate(
        order(amount="2", symbol="NEW"), portfolio
    )
    assert not risk.approved
    assert risk.reason == "MAX_TOTAL_EXPOSURE"


@pytest.mark.unit
def test_invalid_price_is_rejected(portfolio, paper_config):
    risk = HardRiskEngine(paper_config).evaluate(order(price="0"), portfolio)
    assert not risk.approved
    assert risk.reason == "INVALID_PRICE"


@pytest.mark.unit
def test_invalid_action_is_rejected(portfolio, paper_config):
    risk = HardRiskEngine(paper_config).evaluate(order(action="WAIT"), portfolio)
    assert not risk.approved
    assert risk.reason == "INVALID_ACTION"


@pytest.mark.unit
def test_negative_amount_is_rejected(portfolio, paper_config):
    risk = HardRiskEngine(paper_config).evaluate(order(amount="-1"), portfolio)
    assert not risk.approved
    assert risk.reason == "NEGATIVE_AMOUNT"


@pytest.mark.unit
def test_minimum_order_value(portfolio, paper_config):
    risk = HardRiskEngine(paper_config).evaluate(order(amount="0.99"), portfolio)
    assert not risk.approved
    assert risk.reason == "MIN_ORDER_VALUE"


class FakePriceService:
    def __init__(self, price="100"):
        self.price = Decimal(price)
        self.calls = 0

    def get_price_eur(self, symbol, as_of):
        self.calls += 1
        return MarketPrice(
            symbol=symbol,
            price_eur=self.price,
            source_price=self.price,
            source_currency="EUR",
            fx_rate_to_eur=Decimal("1"),
            as_of=as_of,
        )


class UnavailablePriceService:
    def get_price_eur(self, symbol, as_of):
        raise PriceUnavailableError("PRICE_UNAVAILABLE")


@pytest.mark.unit
def test_graph_node_executes_buy_and_returns_visible_report(paper_config):
    price_service = FakePriceService()
    node = PaperTradingNode(paper_config, price_service=price_service)
    result = node(
        {
            "company_of_interest": "BTC-USD",
            "trade_date": "2026-09-17",
            "final_trade_decision": "**Rating**: Buy\n\nEnter gradually.",
        }
    )
    audit = result["paper_trading_result"]
    assert audit["status"] == "FILLED"
    assert audit["risk_decision"]["approved"] is True
    assert audit["portfolio"]["cash_balance"] == pytest.approx(47.4975)
    assert "# PAPER PORTFOLIO" in result["paper_trading_report"]


@pytest.mark.unit
def test_graph_node_hold_does_not_fetch_price_or_trade(paper_config):
    price_service = FakePriceService()
    node = PaperTradingNode(paper_config, price_service=price_service)
    result = node(
        {
            "company_of_interest": "BTC-USD",
            "trade_date": "2026-09-17",
            "final_trade_decision": "**Rating**: Hold",
        }
    )
    assert result["paper_trading_result"]["status"] == "NO_ACTION"
    assert price_service.calls == 0
    assert VirtualPortfolio.load(paper_config.portfolio_path).trade_history == []


@pytest.mark.unit
def test_graph_node_fails_closed_when_price_is_unavailable(paper_config):
    node = PaperTradingNode(paper_config, price_service=UnavailablePriceService())
    result = node(
        {
            "company_of_interest": "BTC-USD",
            "trade_date": "2026-09-17",
            "final_trade_decision": "**Rating**: Buy",
        }
    )["paper_trading_result"]
    assert result["status"] == "REJECTED"
    assert result["reason"] == "PRICE_UNAVAILABLE"
    assert VirtualPortfolio.load(paper_config.portfolio_path).trade_history == []


@pytest.mark.unit
def test_graph_node_fails_closed_when_portfolio_json_is_corrupt(paper_config):
    paper_config.portfolio_path.parent.mkdir(parents=True, exist_ok=True)
    paper_config.portfolio_path.write_text("not-json", encoding="utf-8")
    node = PaperTradingNode(paper_config, price_service=FakePriceService())
    result = node(
        {
            "company_of_interest": "BTC-USD",
            "trade_date": "2026-09-17",
            "final_trade_decision": "**Rating**: Buy",
        }
    )["paper_trading_result"]
    assert result["status"] == "REJECTED"
    assert result["reason"] == "PORTFOLIO_UNAVAILABLE"


@pytest.mark.unit
def test_graph_routes_portfolio_manager_through_paper_trading():
    workflow = GraphSetup(
        MagicMock(),
        MagicMock(),
        TradingAgentsGraph._create_tool_nodes(None),
        ConditionalLogic(),
    ).setup_graph(["market"])
    assert ("Portfolio Manager", "Paper Trading") in workflow.edges
    assert ("Paper Trading", "__end__") in workflow.edges
    assert ("Portfolio Manager", "__end__") not in workflow.edges
