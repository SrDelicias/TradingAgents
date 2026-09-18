"""Phase 2 tests: valuation, lifecycle actions, exits, drawdown, and persistence."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from dashboard.utils.data import load_events
from tradingagents.paper_trading import (
    HardRiskEngine,
    PaperAction,
    PaperBroker,
    PaperOrderRequest,
    PaperTradingConfig,
    VirtualPortfolio,
)
from tradingagents.paper_trading.models import MarketPrice
from tradingagents.paper_trading.node import PaperTradingNode
from tradingagents.paper_trading.price_service import PriceUnavailableError

NOW = "2026-09-17T12:00:00+00:00"


class MutablePriceProvider:
    def __init__(self, prices: dict[str, str] | None = None):
        self.prices = {k: Decimal(v) for k, v in (prices or {}).items()}
        self.fail: set[str] = set()

    def set(self, symbol: str, price: str) -> None:
        self.prices[symbol] = Decimal(price)

    def get_price_eur(self, symbol: str, as_of: str) -> MarketPrice:
        if symbol in self.fail or symbol not in self.prices:
            raise PriceUnavailableError("PRICE_UNAVAILABLE")
        price = self.prices[symbol]
        return MarketPrice(
            symbol=symbol,
            price_eur=price,
            source_price=price,
            source_currency="EUR",
            fx_rate_to_eur=Decimal("1"),
            as_of=as_of,
        )


@pytest.fixture()
def config(tmp_path):
    return PaperTradingConfig(
        portfolio_path=tmp_path / "paper_portfolio.json",
        equity_history_path=tmp_path / "equity_history.json",
        events_path=tmp_path / "events.jsonl",
    )


def request(action, amount, price, symbol="BTC-USD", reason="TEST"):
    return PaperOrderRequest(
        symbol=symbol,
        action=action,
        requested_allocation=Decimal(str(amount)),
        reference_price=Decimal(str(price)),
        timestamp=NOW,
        source_decision=f"Rating: {action}",
        reason=reason,
    )


def execute(portfolio, config, action, amount, price, symbol="BTC-USD", reason="TEST"):
    order = request(action, amount, price, symbol, reason)
    risk = HardRiskEngine(config).evaluate(order, portfolio)
    result = PaperBroker(config).execute(order, risk, portfolio)
    assert risk.approved, risk.reason
    assert result.executed, result.reason
    return result.trade


def run_node(node, rating, symbol="BTC-USD"):
    return node(
        {
            "company_of_interest": symbol,
            "trade_date": "2026-09-17",
            "final_trade_decision": f"**Rating**: {rating}",
        }
    )["paper_trading_result"]


@pytest.mark.unit
def test_mark_to_market_updates_price_and_unrealized_pnl(config):
    portfolio = VirtualPortfolio(config.portfolio_path, config=config)
    execute(portfolio, config, PaperAction.BUY, "2.5", "100")
    provider = MutablePriceProvider({"BTC-USD": "110"})
    result = portfolio.mark_to_market(provider, "2026-09-17")
    position = portfolio.positions["BTC-USD"]
    assert result == {"BTC-USD": True}
    assert position.current_price == Decimal("110")
    assert position.unrealized_pnl > 0
    assert portfolio.total_equity > Decimal("50")
    assert position.last_price_update == "2026-09-17"
    assert not position.price_stale


@pytest.mark.unit
def test_stale_price_keeps_last_price_and_blocks_increase(config):
    portfolio = VirtualPortfolio(config.portfolio_path, config=config)
    execute(portfolio, config, PaperAction.BUY, "2.5", "100")
    previous = portfolio.positions["BTC-USD"].current_price
    provider = MutablePriceProvider()
    provider.fail.add("BTC-USD")
    portfolio.mark_to_market(provider, "2026-09-17")
    position = portfolio.positions["BTC-USD"]
    assert position.current_price == previous
    assert position.price_stale
    risk = HardRiskEngine(config).evaluate(
        request(PaperAction.INCREASE, "1", previous), portfolio
    )
    assert not risk.approved
    assert risk.reason == "STALE_PRICE"


@pytest.mark.unit
def test_equity_history_avoids_duplicate_snapshots(config):
    portfolio = VirtualPortfolio(config.portfolio_path, config=config)
    assert portfolio.save_equity_snapshot("2026-09-17T10:00:00+00:00")
    assert not portfolio.save_equity_snapshot("2026-09-17T11:00:00+00:00")
    assert len(portfolio.load_equity_history()) == 1


@pytest.mark.unit
def test_high_water_mark_and_drawdowns(config):
    portfolio = VirtualPortfolio(config.portfolio_path, config=config)
    execute(portfolio, config, PaperAction.BUY, "2.5", "100")
    provider = MutablePriceProvider({"BTC-USD": "120"})
    portfolio.mark_to_market(provider, "2026-09-17")
    high = portfolio.high_water_mark
    assert high == portfolio.total_equity
    provider.set("BTC-USD", "80")
    portfolio.mark_to_market(provider, "2026-09-17")
    expected = (high - portfolio.total_equity) / high
    assert portfolio.current_drawdown_percent == expected
    assert portfolio.max_drawdown_percent == expected


@pytest.mark.unit
def test_increase_recalculates_weighted_average_entry(config):
    portfolio = VirtualPortfolio(config.portfolio_path, config=config)
    first = execute(portfolio, config, PaperAction.BUY, "2.5", "100")
    position = portfolio.positions["BTC-USD"]
    old_quantity = position.quantity
    old_entry = position.entry_price
    second = execute(portfolio, config, PaperAction.INCREASE, "2", "110")
    expected = (
        old_quantity * old_entry + second.quantity * second.execution_price
    ) / (old_quantity + second.quantity)
    assert portfolio.positions["BTC-USD"].entry_price == expected
    assert portfolio.positions["BTC-USD"].quantity == first.quantity + second.quantity


@pytest.mark.unit
def test_partial_sell_reduces_half_and_realizes_correct_pnl(config):
    portfolio = VirtualPortfolio(config.portfolio_path, config=config)
    execute(portfolio, config, PaperAction.BUY, "2.5", "100")
    position = portfolio.positions["BTC-USD"]
    original_quantity = position.quantity
    portfolio.update_price("BTC-USD", Decimal("110"))
    trade = execute(
        portfolio,
        config,
        PaperAction.REDUCE,
        position.market_value * config.reduce_position_percent,
        "110",
    )
    assert trade.quantity == original_quantity * Decimal("0.50")
    assert portfolio.positions["BTC-USD"].quantity == original_quantity * Decimal("0.50")
    expected = trade.total_value - trade.fees - trade.quantity * position.entry_price
    assert trade.realized_pnl == expected


@pytest.mark.unit
def test_overweight_increases_existing_position(config):
    provider = MutablePriceProvider({"BTC-USD": "100"})
    node = PaperTradingNode(config, provider)
    run_node(node, "Buy")
    before = VirtualPortfolio.load(config.portfolio_path, config=config).positions["BTC-USD"].quantity
    result = run_node(node, "Overweight")
    after = VirtualPortfolio.load(config.portfolio_path, config=config).positions["BTC-USD"].quantity
    assert result["decision"] == "INCREASE"
    assert result["status"] == "FILLED"
    assert after > before


@pytest.mark.unit
def test_buy_on_existing_position_becomes_increase(config):
    provider = MutablePriceProvider({"BTC-USD": "100"})
    node = PaperTradingNode(config, provider)
    run_node(node, "Buy")
    result = run_node(node, "Buy")
    assert result["requested_decision"] == "BUY"
    assert result["decision"] == "INCREASE"


@pytest.mark.unit
def test_underweight_reduces_position_by_half(config):
    provider = MutablePriceProvider({"BTC-USD": "100"})
    node = PaperTradingNode(config, provider)
    run_node(node, "Buy")
    before = VirtualPortfolio.load(config.portfolio_path, config=config).positions["BTC-USD"].quantity
    result = run_node(node, "Underweight")
    portfolio = VirtualPortfolio.load(config.portfolio_path, config=config)
    assert result["decision"] == "REDUCE"
    assert portfolio.positions["BTC-USD"].quantity == pytest.approx(
        before * Decimal("0.50")
    )


@pytest.mark.unit
def test_sell_fully_closes_position(config):
    provider = MutablePriceProvider({"BTC-USD": "100"})
    node = PaperTradingNode(config, provider)
    run_node(node, "Buy")
    result = run_node(node, "Sell")
    assert result["decision"] == "CLOSE"
    assert "BTC-USD" not in VirtualPortfolio.load(config.portfolio_path, config=config).positions


@pytest.mark.unit
def test_stop_loss_closes_without_llm_instruction(config):
    provider = MutablePriceProvider({"BTC-USD": "100"})
    node = PaperTradingNode(config, provider)
    run_node(node, "Buy")
    provider.set("BTC-USD", "97")
    result = run_node(node, "Hold")
    assert result["reason"] == "STOP_LOSS"
    assert result["execution"]["trade"]["reason"] == "STOP_LOSS"
    assert "BTC-USD" not in result["portfolio"]["positions"]
    assert "STOP_LOSS_TRIGGERED" in {
        event["type"] for event in load_events(config.events_path)
    }


@pytest.mark.unit
def test_take_profit_closes_without_llm_instruction(config):
    provider = MutablePriceProvider({"BTC-USD": "100"})
    node = PaperTradingNode(config, provider)
    run_node(node, "Buy")
    provider.set("BTC-USD", "107")
    result = run_node(node, "Hold")
    assert result["reason"] == "TAKE_PROFIT"
    assert result["execution"]["trade"]["reason"] == "TAKE_PROFIT"


@pytest.mark.unit
def test_trailing_stop_tracks_high_and_closes(config):
    trailing_config = replace(
        config,
        trailing_stop_enabled=True,
        take_profit_percent=Decimal("1.00"),
    )
    provider = MutablePriceProvider({"BTC-USD": "100"})
    node = PaperTradingNode(trailing_config, provider)
    run_node(node, "Buy")
    provider.set("BTC-USD", "110")
    hold = run_node(node, "Hold")
    assert "BTC-USD" in hold["portfolio"]["positions"]
    assert Decimal(
        hold["portfolio"]["positions"]["BTC-USD"]["highest_price_since_entry"]
    ) == Decimal("110")
    provider.set("BTC-USD", "106")
    result = run_node(node, "Hold")
    assert result["reason"] == "TRAILING_STOP"


@pytest.mark.unit
def test_max_drawdown_blocks_buy_but_allows_close(config):
    portfolio = VirtualPortfolio(config.portfolio_path, config=config)
    execute(portfolio, config, PaperAction.BUY, "2.5", "100")
    portfolio.high_water_mark = Decimal("60")
    portfolio.refresh_metrics()
    buy_risk = HardRiskEngine(config).evaluate(
        request(PaperAction.INCREASE, "1", "100"), portfolio
    )
    assert not buy_risk.approved
    assert buy_risk.reason == "MAX_DRAWDOWN"
    position = portfolio.positions["BTC-USD"]
    close_risk = HardRiskEngine(config).evaluate(
        request(PaperAction.CLOSE, position.market_value, position.current_price),
        portfolio,
    )
    assert close_risk.approved


@pytest.mark.unit
@pytest.mark.parametrize("action", [PaperAction.INCREASE, PaperAction.REDUCE])
def test_position_dependent_actions_require_an_existing_position(config, action):
    portfolio = VirtualPortfolio(config.portfolio_path, config=config)
    risk = HardRiskEngine(config).evaluate(request(action, "1", "100"), portfolio)
    assert not risk.approved
    assert risk.reason == "POSITION_NOT_FOUND"


@pytest.mark.unit
def test_tiny_position_can_always_be_closed(config):
    tiny_config = replace(config, min_order_value_eur=Decimal("10"))
    portfolio = VirtualPortfolio(tiny_config.portfolio_path, config=tiny_config)
    tiny_config = replace(tiny_config, min_order_value_eur=Decimal("0.01"))
    execute(portfolio, tiny_config, PaperAction.BUY, "1", "100")
    strict_config = replace(tiny_config, min_order_value_eur=Decimal("10"))
    position = portfolio.positions["BTC-USD"]
    risk = HardRiskEngine(strict_config).evaluate(
        request(PaperAction.CLOSE, position.market_value, position.current_price),
        portfolio,
    )
    assert risk.approved


@pytest.mark.unit
def test_invalid_llm_decision_cannot_suppress_stop_loss(config):
    provider = MutablePriceProvider({"BTC-USD": "100"})
    node = PaperTradingNode(config, provider)
    run_node(node, "Buy")
    provider.set("BTC-USD", "97")
    result = run_node(node, "Definitely not a rating")
    assert result["reason"] == "STOP_LOSS"
    assert "BTC-USD" not in result["portfolio"]["positions"]


@pytest.mark.unit
def test_persistence_across_multiple_program_instances(config):
    first = VirtualPortfolio(config.portfolio_path, config=config)
    execute(first, config, PaperAction.BUY, "2.5", "100")
    second = VirtualPortfolio.load(config.portfolio_path, config=config)
    execute(second, config, PaperAction.INCREASE, "1", "101")
    third = VirtualPortfolio.load(config.portfolio_path, config=config)
    assert len(third.trade_history) == 2
    assert third.positions["BTC-USD"].quantity > first.positions["BTC-USD"].quantity
    assert third.high_water_mark >= third.initial_balance


@pytest.mark.unit
def test_multirun_integration_lifecycle(config):
    """50 EUR -> Buy -> Overweight -> Underweight -> Sell, all on temp files."""
    provider = MutablePriceProvider({"BTC-USD": "100"})
    node = PaperTradingNode(config, provider)
    buy = run_node(node, "Buy")
    provider.set("BTC-USD", "103")
    increase = run_node(node, "Overweight")
    provider.set("BTC-USD", "105")
    reduce = run_node(node, "Underweight")
    provider.set("BTC-USD", "101")
    close = run_node(node, "Sell")

    portfolio = VirtualPortfolio.load(config.portfolio_path, config=config)
    assert [buy["decision"], increase["decision"], reduce["decision"], close["decision"]] == [
        "BUY",
        "INCREASE",
        "REDUCE",
        "CLOSE",
    ]
    assert [trade.action for trade in portfolio.trade_history] == [
        PaperAction.BUY,
        PaperAction.INCREASE,
        PaperAction.REDUCE,
        PaperAction.CLOSE,
    ]
    assert not portfolio.positions
    assert portfolio.cash_balance >= 0
    assert len(portfolio.load_equity_history()) == 4


@pytest.mark.unit
def test_execution_publishes_structured_dashboard_events(config):
    provider = MutablePriceProvider({"BTC-USD": "100"})
    run_node(PaperTradingNode(config, provider), "Buy")
    events = load_events(config.events_path)
    types = {event["type"] for event in events}
    assert {
        "PORTFOLIO_DECISION",
        "DECISION_NORMALIZED",
        "ORDER_CREATED",
        "RISK_APPROVED",
        "ORDER_EXECUTED",
        "POSITION_OPENED",
        "PORTFOLIO_UPDATED",
        "ANALYSIS_COMPLETED",
    } <= types
    assert all("metadata" in event for event in events)
