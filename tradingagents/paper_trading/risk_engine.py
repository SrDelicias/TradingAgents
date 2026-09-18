"""Deterministic hard-risk checks. This module deliberately contains no LLM."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from .config import PaperTradingConfig
from .models import ZERO, PaperAction, PaperOrderRequest, RiskDecision, parse_paper_action
from .portfolio import VirtualPortfolio


class HardRiskEngine:
    def __init__(self, config: PaperTradingConfig | None = None) -> None:
        self.config = config or PaperTradingConfig()

    def evaluate(
        self,
        order: PaperOrderRequest,
        portfolio: VirtualPortfolio,
    ) -> RiskDecision:
        portfolio.refresh_metrics()
        metrics = self._metrics(portfolio, order.timestamp)

        try:
            action = parse_paper_action(order.action)
        except ValueError:
            return self._reject("INVALID_ACTION", metrics)

        try:
            price = Decimal(str(order.reference_price))
            amount = Decimal(str(order.requested_allocation))
        except (InvalidOperation, ValueError):
            return self._reject("INVALID_NUMERIC_VALUE", metrics)

        if not price.is_finite() or price <= ZERO:
            return self._reject("INVALID_PRICE", metrics)
        if not amount.is_finite() or amount < ZERO:
            return self._reject("NEGATIVE_AMOUNT", metrics)
        if action is PaperAction.HOLD:
            return self._reject("HOLD", metrics)
        # Risk-reducing orders remain executable even when their value is
        # below the normal entry-size floor.
        if (
            action in {PaperAction.BUY, PaperAction.INCREASE}
            and amount < self.config.min_order_value_eur
        ):
            return self._reject("MIN_ORDER_VALUE", metrics)

        if action in {PaperAction.BUY, PaperAction.INCREASE}:
            return self._evaluate_buy(order, action, amount, portfolio, metrics)
        return self._evaluate_sell(order, action, amount, price, portfolio, metrics)

    def _evaluate_buy(
        self,
        order: PaperOrderRequest,
        action: PaperAction,
        amount: Decimal,
        portfolio: VirtualPortfolio,
        metrics: dict,
    ) -> RiskDecision:
        symbol = order.symbol.upper()
        current = portfolio.positions.get(symbol)
        if action is PaperAction.BUY and current is not None:
            return self._reject("POSITION_EXISTS", metrics)
        if action is PaperAction.INCREASE and current is None:
            return self._reject("POSITION_NOT_FOUND", metrics)
        if current is not None and current.price_stale:
            return self._reject("STALE_PRICE", metrics)
        if portfolio.current_drawdown_percent >= self.config.max_drawdown_percent:
            return self._reject("MAX_DRAWDOWN", metrics)
        if not self.config.allow_leverage and portfolio.cash_balance < ZERO:
            return self._reject("NEGATIVE_BALANCE", metrics)

        daily_limit = portfolio.initial_balance * self.config.max_daily_loss_percent
        if portfolio.daily_pnl(order.timestamp) <= -daily_limit:
            return self._reject("MAX_DAILY_LOSS", metrics)

        estimated_debit = amount * (Decimal("1") + self.config.trading_fee_percent)
        if estimated_debit > portfolio.cash_balance:
            return self._reject("INSUFFICIENT_FUNDS", metrics)
        if current is None and len(portfolio.positions) >= self.config.max_open_positions:
            return self._reject("MAX_OPEN_POSITIONS", metrics)

        position_value = current.market_value if current else ZERO
        max_position_value = portfolio.total_equity * self.config.max_position_percent
        projected_position = position_value + amount
        if projected_position > max_position_value:
            return self._reject("MAX_POSITION_SIZE", metrics)

        max_exposure = portfolio.total_equity * self.config.max_total_exposure_percent
        projected_exposure = portfolio.total_exposure + amount
        if projected_exposure > max_exposure:
            return self._reject("MAX_TOTAL_EXPOSURE", metrics)

        metrics = {
            **metrics,
            "projected_position_value": float(projected_position),
            "projected_total_exposure": float(projected_exposure),
        }
        return RiskDecision(
            approved=True,
            reason="APPROVED",
            approved_amount=amount,
            risk_metrics=metrics,
        )

    def _evaluate_sell(
        self,
        order: PaperOrderRequest,
        action: PaperAction,
        amount: Decimal,
        price: Decimal,
        portfolio: VirtualPortfolio,
        metrics: dict,
    ) -> RiskDecision:
        position = portfolio.positions.get(order.symbol.upper())
        if position is None:
            return self._reject("POSITION_NOT_FOUND", metrics)
        requested_quantity = amount / price
        if requested_quantity <= ZERO:
            return self._reject("INVALID_QUANTITY", metrics)
        tolerance = Decimal("0.000000000000000001")
        if requested_quantity - position.quantity > tolerance:
            return self._reject("INSUFFICIENT_POSITION", metrics)
        approved_amount = position.quantity * price if action is PaperAction.CLOSE else amount
        return RiskDecision(
            approved=True,
            reason="APPROVED",
            approved_amount=approved_amount,
            risk_metrics={
                **metrics,
                "projected_total_exposure": float(
                    max(ZERO, portfolio.total_exposure - approved_amount)
                ),
            },
        )

    @staticmethod
    def _reject(reason: str, metrics: dict) -> RiskDecision:
        return RiskDecision(
            approved=False,
            reason=reason,
            approved_amount=ZERO,
            risk_metrics=metrics,
        )

    @staticmethod
    def _metrics(portfolio: VirtualPortfolio, timestamp: str) -> dict:
        return {
            "cash_balance": float(portfolio.cash_balance),
            "total_equity": float(portfolio.total_equity),
            "total_exposure": float(portfolio.total_exposure),
            "exposure_percent": (
                float(portfolio.total_exposure / portfolio.total_equity)
                if portfolio.total_equity > ZERO
                else 0.0
            ),
            "daily_pnl": float(portfolio.daily_pnl(timestamp)),
            "open_positions": len(portfolio.positions),
            "current_drawdown_percent": float(portfolio.current_drawdown_percent),
            "max_drawdown_percent": float(portfolio.max_drawdown_percent),
        }
