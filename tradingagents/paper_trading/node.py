"""LangGraph node implementing the complete local paper-portfolio lifecycle."""

from __future__ import annotations

from decimal import Decimal

from tradingagents.agents.utils.rating import extract_rating
from tradingagents.monitoring import EventLog

from .broker import PaperBroker
from .config import PaperTradingConfig
from .models import PaperAction, PaperOrderRequest, normalize_decision, utc_now_iso
from .portfolio import VirtualPortfolio
from .price_service import MarketPriceService, PriceUnavailableError
from .risk_engine import HardRiskEngine


class PaperTradingNode:
    def __init__(
        self,
        config: PaperTradingConfig,
        price_service: MarketPriceService | None = None,
    ) -> None:
        self.config = config
        self.price_service = price_service or MarketPriceService()
        self.risk_engine = HardRiskEngine(config)
        self.broker = PaperBroker(config)
        self.events = EventLog(config.events_path)

    def __call__(self, state) -> dict:
        symbol = str(state["company_of_interest"]).upper()
        if not self.config.enabled:
            self._emit_analysis_completed(symbol, "NO_ACTION", "PAPER_TRADING_DISABLED")
            return self._result(
                symbol=symbol,
                action="DISABLED",
                status="NO_ACTION",
                reason="PAPER_TRADING_DISABLED",
            )

        source_decision = state.get("final_trade_decision", "")
        rating = extract_rating(source_decision)
        self.events.emit(
            "PORTFOLIO_DECISION",
            stage="Portfolio Manager",
            symbol=symbol,
            status="COMPLETED",
            message=f"Portfolio Manager → {rating or 'INVALID'}",
            metadata={"decision": rating or "INVALID"},
        )

        try:
            portfolio = VirtualPortfolio(
                self.config.portfolio_path,
                initial_balance=self.config.initial_balance,
                equity_history_path=self.config.equity_history_path,
                config=self.config,
            )
        except Exception:  # noqa: BLE001 - execution must fail closed, not break analysis
            self._emit_analysis_completed(symbol, "ERROR", "PORTFOLIO_UNAVAILABLE")
            return self._result(
                symbol=symbol,
                action=rating or "INVALID",
                status="REJECTED",
                reason="PORTFOLIO_UNAVAILABLE",
            )

        # The whole portfolio is valued before any new decision. Failed symbols
        # keep their last known price and become stale.
        try:
            valuations = portfolio.mark_to_market(self.price_service, state["trade_date"])
            for position_symbol, fresh in valuations.items():
                position = portfolio.positions[position_symbol]
                self.events.emit(
                    "MARKET_PRICE_UPDATED",
                    stage="Mark to Market",
                    symbol=position_symbol,
                    status="COMPLETED" if fresh else "WARNING",
                    message=(
                        f"{position_symbol} price → {position.current_price} EUR"
                        if fresh
                        else f"{position_symbol} price unavailable; using stale value"
                    ),
                    metadata={
                        "price": str(position.current_price),
                        "stale": not fresh,
                    },
                )
            automatic_executions = self._execute_automatic_exits(portfolio)
        except Exception:  # noqa: BLE001 - never continue from an uncertain portfolio
            self._emit_analysis_completed(symbol, "ERROR", "PORTFOLIO_UNAVAILABLE")
            return self._result(
                symbol=symbol,
                action=rating or "INVALID",
                status="REJECTED",
                reason="PORTFOLIO_UNAVAILABLE",
            )

        # An automatic exit for the analyzed symbol wins this cycle; reopening
        # it immediately from the older PM recommendation would defeat the stop.
        same_symbol_exit = next(
            (
                item
                for item in automatic_executions
                if ((item.get("execution") or {}).get("trade") or {}).get("symbol")
                == symbol
            ),
            None,
        )
        if same_symbol_exit:
            portfolio.save_equity_snapshot()
            self._emit_portfolio_updated(symbol, portfolio)
            self._emit_analysis_completed(symbol, "COMPLETED", same_symbol_exit["reason"])
            result = {
                "symbol": symbol,
                "decision": same_symbol_exit["action"],
                "status": same_symbol_exit["execution"]["status"],
                "reason": same_symbol_exit["reason"],
                "automatic_executions": automatic_executions,
                "risk_decision": same_symbol_exit["risk_decision"],
                "execution": same_symbol_exit["execution"],
                "portfolio": portfolio.snapshot(),
            }
            return self._rendered(result)

        # Decision parsing deliberately happens after valuation and automatic
        # exits so a malformed LLM response can never suppress a hard stop.
        try:
            requested_action = normalize_decision(rating or "")
        except ValueError:
            portfolio.save_equity_snapshot()
            self._emit_portfolio_updated(symbol, portfolio)
            self._emit_analysis_completed(symbol, "ERROR", "INVALID_DECISION")
            return self._result(
                symbol=symbol,
                action="INVALID",
                status="REJECTED",
                reason="INVALID_DECISION",
                portfolio=portfolio.snapshot(),
                automatic_executions=automatic_executions,
            )

        action = self._effective_action(requested_action, symbol, portfolio)
        self.events.emit(
            "DECISION_NORMALIZED",
            stage="Decision Normalizer",
            symbol=symbol,
            status="COMPLETED",
            message=f"Decision normalized → {action.value}",
            metadata={
                "portfolio_decision": rating,
                "requested_action": requested_action.value,
                "normalized_action": action.value,
            },
        )
        if action is PaperAction.HOLD:
            portfolio.save_equity_snapshot()
            self._emit_portfolio_updated(symbol, portfolio)
            self._emit_analysis_completed(symbol, "COMPLETED", "HOLD")
            return self._result(
                symbol=symbol,
                action=action.value,
                status="NO_ACTION",
                reason="HOLD",
                portfolio=portfolio.snapshot(),
                automatic_executions=automatic_executions,
            )

        try:
            market_price = self.price_service.get_price_eur(symbol, state["trade_date"])
        except PriceUnavailableError:
            portfolio.save_equity_snapshot()
            self._emit_portfolio_updated(symbol, portfolio)
            self._emit_analysis_completed(symbol, "ERROR", "PRICE_UNAVAILABLE")
            return self._result(
                symbol=symbol,
                action=action.value,
                status="REJECTED",
                reason="PRICE_UNAVAILABLE",
                portfolio=portfolio.snapshot(),
                automatic_executions=automatic_executions,
            )

        self.events.emit(
            "MARKET_PRICE_UPDATED",
            stage="Mark to Market",
            symbol=symbol,
            status="COMPLETED",
            message=f"{symbol} price → {market_price.price_eur} EUR",
            metadata={"price": str(market_price.price_eur), "stale": False},
        )

        try:
            portfolio.update_price(
                symbol,
                market_price.price_eur,
                timestamp=market_price.as_of,
                persist=True,
            )
        except Exception:  # noqa: BLE001 - never trade when state cannot be persisted
            self._emit_analysis_completed(symbol, "ERROR", "PORTFOLIO_UNAVAILABLE")
            return self._result(
                symbol=symbol,
                action=action.value,
                status="REJECTED",
                reason="PORTFOLIO_UNAVAILABLE",
                portfolio=portfolio.snapshot(),
                automatic_executions=automatic_executions,
            )

        requested = self._requested_amount(action, symbol, portfolio)
        order = PaperOrderRequest(
            symbol=symbol,
            action=action,
            requested_allocation=requested,
            reference_price=market_price.price_eur,
            timestamp=utc_now_iso(),
            source_decision=source_decision,
            reason="PORTFOLIO_MANAGER",
        )
        self.events.emit(
            "ORDER_CREATED",
            stage="Hard Risk Engine",
            symbol=symbol,
            status="RUNNING",
            message=f"{action.value} order created for {requested:.4f} EUR",
            metadata={"action": action.value, "requested_amount": str(requested)},
        )
        risk = self.risk_engine.evaluate(order, portfolio)
        self._emit_risk(symbol, risk)
        execution = self.broker.execute(order, risk, portfolio) if risk.approved else None
        if execution and execution.executed and execution.trade:
            self._emit_execution(execution.trade.model_dump(mode="json"))
        portfolio.save_equity_snapshot()
        self._emit_portfolio_updated(symbol, portfolio)
        status = execution.status if execution else "REJECTED"
        reason = execution.reason if execution and execution.reason else risk.reason
        self._emit_analysis_completed(symbol, "COMPLETED", reason)
        result = {
            "symbol": symbol,
            "requested_decision": requested_action.value,
            "decision": action.value,
            "price": float(market_price.price_eur),
            "market_price": market_price.model_dump(mode="json"),
            "requested_amount": float(requested),
            "risk_decision": risk.model_dump(mode="json"),
            "execution": execution.model_dump(mode="json") if execution else None,
            "automatic_executions": automatic_executions,
            "status": status,
            "reason": reason,
            "portfolio": portfolio.snapshot(),
        }
        return self._rendered(result)

    @staticmethod
    def _effective_action(
        action: PaperAction,
        symbol: str,
        portfolio: VirtualPortfolio,
    ) -> PaperAction:
        if action is PaperAction.BUY and symbol in portfolio.positions:
            return PaperAction.INCREASE
        return action

    def _requested_amount(
        self,
        action: PaperAction,
        symbol: str,
        portfolio: VirtualPortfolio,
    ) -> Decimal:
        position = portfolio.positions.get(symbol)
        if action is PaperAction.BUY:
            return portfolio.total_equity * self.config.buy_allocation_percent
        if action is PaperAction.INCREASE:
            desired = portfolio.total_equity * self.config.increase_allocation_percent
            if position is None:
                return desired
            position_room = max(
                Decimal("0"),
                portfolio.total_equity * self.config.max_position_percent
                - position.market_value,
            )
            exposure_room = max(
                Decimal("0"),
                portfolio.total_equity * self.config.max_total_exposure_percent
                - portfolio.total_exposure,
            )
            return min(desired, position_room, exposure_room)
        if position is None:
            return self.config.min_order_value_eur
        if action is PaperAction.REDUCE:
            return position.market_value * self.config.reduce_position_percent
        return position.market_value

    def _execute_automatic_exits(self, portfolio: VirtualPortfolio) -> list[dict]:
        executions = []
        for symbol, position in list(portfolio.positions.items()):
            if position.price_stale:
                continue
            reason = None
            if position.stop_loss_price is not None and position.current_price <= position.stop_loss_price:
                reason = "STOP_LOSS"
            elif (
                position.take_profit_price is not None
                and position.current_price >= position.take_profit_price
            ):
                reason = "TAKE_PROFIT"
            elif self.config.trailing_stop_enabled:
                highest = position.highest_price_since_entry or position.current_price
                trailing_stop = highest * (
                    Decimal("1") - self.config.trailing_stop_percent
                )
                if position.current_price <= trailing_stop:
                    reason = "TRAILING_STOP"
            if reason is None:
                continue

            self.events.emit(
                f"{reason}_TRIGGERED",
                stage="Automatic Exit",
                symbol=symbol,
                status="WARNING",
                message=f"{reason.replace('_', ' ').title()} triggered",
                metadata={"price": str(position.current_price)},
            )

            order = PaperOrderRequest(
                symbol=symbol,
                action=PaperAction.CLOSE,
                requested_allocation=position.market_value,
                reference_price=position.current_price,
                timestamp=utc_now_iso(),
                source_decision=f"Automatic {reason}",
                reason=reason,
            )
            self.events.emit(
                "ORDER_CREATED",
                stage="Hard Risk Engine",
                symbol=symbol,
                status="RUNNING",
                message=f"Automatic CLOSE created for {symbol}",
                metadata={"action": "CLOSE", "reason": reason},
            )
            risk = self.risk_engine.evaluate(order, portfolio)
            self._emit_risk(symbol, risk)
            execution = self.broker.execute(order, risk, portfolio) if risk.approved else None
            if execution and execution.executed and execution.trade:
                self._emit_execution(execution.trade.model_dump(mode="json"))
            executions.append(
                {
                    "symbol": symbol,
                    "action": PaperAction.CLOSE.value,
                    "reason": reason,
                    "risk_decision": risk.model_dump(mode="json"),
                    "execution": execution.model_dump(mode="json") if execution else None,
                }
            )
        return executions

    def _emit_risk(self, symbol: str, risk) -> None:
        event_type = "RISK_APPROVED" if risk.approved else "RISK_REJECTED"
        status = "COMPLETED" if risk.approved else "REJECTED"
        self.events.emit(
            event_type,
            stage="Hard Risk Engine",
            symbol=symbol,
            status=status,
            message=f"Hard Risk Engine → {'APPROVED' if risk.approved else risk.reason}",
            metadata={
                "approved": risk.approved,
                "reason": risk.reason,
                "approved_amount": str(risk.approved_amount),
            },
        )

    def _emit_execution(self, trade: dict) -> None:
        action = str(trade["action"])
        symbol = str(trade["symbol"])
        self.events.emit(
            "ORDER_EXECUTED",
            stage="Paper Broker",
            symbol=symbol,
            status="COMPLETED",
            message=f"{action} {symbol} {float(trade['total_value']):.2f} EUR",
            metadata=trade,
        )
        position_events = {
            "BUY": "POSITION_OPENED",
            "INCREASE": "POSITION_INCREASED",
            "REDUCE": "POSITION_REDUCED",
            "CLOSE": "POSITION_CLOSED",
        }
        position_verbs = {
            "BUY": "opened",
            "INCREASE": "increased",
            "REDUCE": "reduced",
            "CLOSE": "closed",
        }
        self.events.emit(
            position_events[action],
            stage="Virtual Portfolio",
            symbol=symbol,
            status="COMPLETED",
            message=f"{symbol} position {position_verbs[action]}",
            metadata={"action": action, "reason": trade.get("reason")},
        )

    def _emit_portfolio_updated(self, symbol: str, portfolio: VirtualPortfolio) -> None:
        snapshot = portfolio.snapshot()
        self.events.emit(
            "PORTFOLIO_UPDATED",
            stage="Virtual Portfolio",
            symbol=symbol,
            status="COMPLETED",
            message=f"Portfolio equity → {snapshot['total_equity']:.2f} EUR",
            metadata={
                key: snapshot[key]
                for key in (
                    "cash_balance",
                    "positions_value",
                    "total_equity",
                    "total_pnl",
                    "return_percent",
                    "current_drawdown_percent",
                )
            },
        )

    def _emit_analysis_completed(self, symbol: str, status: str, reason: str) -> None:
        self.events.emit(
            "ANALYSIS_COMPLETED",
            stage="analysis",
            symbol=symbol,
            status=status,
            message=f"{symbol} analysis completed: {reason}",
            metadata={"reason": reason},
        )

    @staticmethod
    def _rendered(result: dict) -> dict:
        return {
            "paper_trading_result": result,
            "paper_trading_report": render_paper_trading_report(result),
        }

    @classmethod
    def _result(
        cls,
        *,
        symbol: str,
        action: str,
        status: str,
        reason: str,
        portfolio: dict | None = None,
        automatic_executions: list[dict] | None = None,
    ) -> dict:
        result = {
            "symbol": symbol,
            "decision": action,
            "status": status,
            "reason": reason,
            "automatic_executions": automatic_executions or [],
            "portfolio": portfolio,
        }
        return cls._rendered(result)


def render_paper_trading_report(result: dict) -> str:
    """Human-readable portfolio and last-execution report."""
    portfolio = result.get("portfolio")
    lines = ["# PAPER PORTFOLIO", ""]
    if portfolio:
        lines.append(f"**Cash:** {portfolio['cash_balance']:.6f} EUR")
        lines.extend(["", "## Positions"])
        if not portfolio["positions"]:
            lines.append("No open positions.")
        for symbol, position in portfolio["positions"].items():
            stale = " STALE" if position.get("price_stale") else ""
            lines.extend([
                f"### {symbol}{stale}",
                f"Quantity: {float(position['quantity']):.12f}",
                f"Average entry: {float(position['entry_price']):.6f} EUR",
                f"Current price: {float(position['current_price']):.6f} EUR",
                f"Market value: {float(position['market_value']):.6f} EUR",
                f"Unrealized P&L: {float(position['unrealized_pnl']):.6f} EUR",
                f"Return: {float(position['return_percent']):.2%}",
            ])
        lines.extend([
            "",
            "## Portfolio",
            f"Initial capital: {portfolio['initial_balance']:.6f} EUR",
            f"Cash: {portfolio['cash_balance']:.6f} EUR",
            f"Invested: {portfolio['positions_value']:.6f} EUR",
            f"Equity: {portfolio['total_equity']:.6f} EUR",
            f"Realized P&L: {portfolio['realized_pnl']:.6f} EUR",
            f"Unrealized P&L: {portfolio['unrealized_pnl']:.6f} EUR",
            f"Total P&L: {portfolio['total_pnl']:.6f} EUR",
            f"Return: {portfolio['return_percent']:.2%}",
            f"Current drawdown: {portfolio['current_drawdown_percent']:.2%}",
            f"Maximum drawdown: {portfolio['max_drawdown_percent']:.2%}",
        ])

    execution = result.get("execution")
    automatic = result.get("automatic_executions") or []
    if execution and execution.get("executed"):
        last = execution["trade"]
    elif automatic and (automatic[-1].get("execution") or {}).get("executed"):
        last = automatic[-1]["execution"]["trade"]
    else:
        last = None
    lines.extend(["", "# LAST EXECUTION", ""])
    if last:
        lines.extend([
            f"Action: {last['action']}",
            f"Symbol: {last['symbol']}",
            f"Execution price: {float(last['execution_price']):.6f} EUR",
            f"Quantity: {float(last['quantity']):.12f}",
            f"Fees: {float(last['fees']):.6f} EUR",
            f"Reason: {last['reason']}",
        ])
    else:
        lines.extend([
            "No execution.",
            f"Decision: {result.get('decision', 'N/A')}",
            f"Reason: {result.get('reason', 'N/A')}",
        ])
    return "\n".join(lines)


def create_paper_trading_node(graph_config: dict | None = None) -> PaperTradingNode:
    return PaperTradingNode(PaperTradingConfig.from_mapping(graph_config))
