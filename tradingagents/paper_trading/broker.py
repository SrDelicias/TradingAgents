"""Cash-only simulated broker. It has no external trading connectivity."""

from __future__ import annotations

from decimal import Decimal

from .config import PaperTradingConfig
from .models import (
    ExecutionResult,
    PaperAction,
    PaperOrderRequest,
    RiskDecision,
    parse_paper_action,
)
from .portfolio import PortfolioError, VirtualPortfolio


class PaperBroker:
    def __init__(self, config: PaperTradingConfig | None = None) -> None:
        self.config = config or PaperTradingConfig()

    def execute(
        self,
        order: PaperOrderRequest,
        risk_decision: RiskDecision,
        portfolio: VirtualPortfolio,
    ) -> ExecutionResult:
        if not risk_decision.approved:
            return ExecutionResult(
                executed=False,
                status="REJECTED",
                reason=risk_decision.reason,
            )

        try:
            action = parse_paper_action(order.action)
        except ValueError:
            return ExecutionResult(executed=False, status="REJECTED", reason="INVALID_ACTION")
        if action is PaperAction.HOLD:
            return ExecutionResult(executed=False, status="NO_ACTION", reason="HOLD")

        reference = Decimal(str(order.reference_price))
        approved_amount = Decimal(str(risk_decision.approved_amount))
        adverse_move = self.config.spread_percent + self.config.slippage_percent
        is_buy = action in {PaperAction.BUY, PaperAction.INCREASE}
        execution_price = (
            reference * (Decimal("1") + adverse_move)
            if is_buy
            else reference * (Decimal("1") - adverse_move)
        )

        if is_buy:
            # approved_amount is the gross position notional; fee is additional.
            quantity = approved_amount / execution_price
            gross_value = quantity * execution_price
        else:
            # A sell amount is expressed at the clean reference price so an
            # adverse execution price never causes the broker to oversell.
            quantity = approved_amount / reference
            gross_value = quantity * execution_price

        fees = gross_value * self.config.trading_fee_percent
        slippage = abs(execution_price - reference) * quantity

        try:
            if is_buy:
                trade = portfolio.apply_buy(
                    symbol=order.symbol,
                    action=action,
                    quantity=quantity,
                    execution_price=execution_price,
                    reference_price=reference,
                    fees=fees,
                    slippage=slippage,
                    timestamp=order.timestamp,
                    reason=order.reason,
                )
            else:
                trade = portfolio.apply_sell(
                    symbol=order.symbol,
                    action=action,
                    quantity=quantity,
                    execution_price=execution_price,
                    reference_price=reference,
                    fees=fees,
                    slippage=slippage,
                    timestamp=order.timestamp,
                    reason=order.reason,
                )
        except PortfolioError as exc:
            return ExecutionResult(executed=False, status="REJECTED", reason=str(exc))

        return ExecutionResult(executed=True, status="FILLED", trade=trade)
