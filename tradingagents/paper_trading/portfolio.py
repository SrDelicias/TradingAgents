"""Persistent virtual portfolio with mark-to-market and equity history."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .config import INITIAL_BALANCE, PaperTradingConfig
from .models import ZERO, EquitySnapshot, PaperAction, Position, TradeRecord, utc_now_iso


class PortfolioError(ValueError):
    """Raised when a broker operation would violate a portfolio invariant."""


class VirtualPortfolio:
    """Cash-only portfolio. It never permits margin, leverage, or short selling."""

    def __init__(
        self,
        path: str | Path,
        initial_balance: Decimal = INITIAL_BALANCE,
        *,
        equity_history_path: str | Path | None = None,
        config: PaperTradingConfig | None = None,
    ) -> None:
        self.path = Path(path)
        self.equity_history_path = (
            Path(equity_history_path)
            if equity_history_path is not None
            else self.path.with_name("equity_history.json")
        )
        self.config = config or PaperTradingConfig(
            initial_balance=Decimal(str(initial_balance)),
            portfolio_path=self.path,
            equity_history_path=self.equity_history_path,
        )
        self.initial_balance = Decimal(str(initial_balance))
        self.cash_balance = self.initial_balance
        self.total_equity = self.initial_balance
        self.realized_pnl = ZERO
        self.unrealized_pnl = ZERO
        self.high_water_mark = self.initial_balance
        self.current_drawdown_percent = ZERO
        self.max_drawdown_percent = ZERO
        self.positions: dict[str, Position] = {}
        self.trade_history: list[TradeRecord] = []

        if self.path.exists():
            self._load()
        else:
            self.save()

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        equity_history_path: str | Path | None = None,
        config: PaperTradingConfig | None = None,
    ) -> VirtualPortfolio:
        return cls(path, equity_history_path=equity_history_path, config=config)

    def _load(self) -> None:
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.initial_balance = Decimal(str(raw["initial_balance"]))
        self.cash_balance = Decimal(str(raw["cash_balance"]))
        self.realized_pnl = Decimal(str(raw.get("realized_pnl", "0")))
        self.high_water_mark = Decimal(
            str(raw.get("high_water_mark", raw.get("total_equity", self.initial_balance)))
        )
        self.current_drawdown_percent = Decimal(
            str(raw.get("current_drawdown_percent", "0"))
        )
        self.max_drawdown_percent = Decimal(str(raw.get("max_drawdown_percent", "0")))
        self.positions = {
            symbol: Position.model_validate(position)
            for symbol, position in raw.get("positions", {}).items()
        }
        self.trade_history = [
            TradeRecord.model_validate(trade) for trade in raw.get("trade_history", [])
        ]
        self.refresh_metrics()
        self._assert_invariants()

    def save(self) -> None:
        self.refresh_metrics()
        self._assert_invariants()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "initial_balance": str(self.initial_balance),
            "cash_balance": str(self.cash_balance),
            "total_equity": str(self.total_equity),
            "realized_pnl": str(self.realized_pnl),
            "unrealized_pnl": str(self.unrealized_pnl),
            "high_water_mark": str(self.high_water_mark),
            "current_drawdown_percent": str(self.current_drawdown_percent),
            "max_drawdown_percent": str(self.max_drawdown_percent),
            "positions": {
                symbol: position.model_dump(mode="json")
                for symbol, position in self.positions.items()
            },
            "trade_history": [trade.model_dump(mode="json") for trade in self.trade_history],
        }
        self._atomic_json_write(self.path, payload)

    @staticmethod
    def _atomic_json_write(path: Path, payload) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp_path.replace(path)

    @property
    def total_exposure(self) -> Decimal:
        return sum((position.market_value for position in self.positions.values()), ZERO)

    @property
    def total_pnl(self) -> Decimal:
        return self.total_equity - self.initial_balance

    @property
    def return_percent(self) -> Decimal:
        return self.total_pnl / self.initial_balance if self.initial_balance > ZERO else ZERO

    def refresh_metrics(self) -> None:
        unrealized = ZERO
        for position in self.positions.values():
            position.invested_amount = position.quantity * position.entry_price
            position.unrealized_pnl = position.market_value - position.invested_amount
            unrealized += position.unrealized_pnl
        self.unrealized_pnl = unrealized
        self.total_equity = self.cash_balance + self.total_exposure
        if self.total_equity > self.high_water_mark:
            self.high_water_mark = self.total_equity
        self.current_drawdown_percent = (
            (self.high_water_mark - self.total_equity) / self.high_water_mark
            if self.high_water_mark > ZERO and self.total_equity < self.high_water_mark
            else ZERO
        )
        if self.current_drawdown_percent > self.max_drawdown_percent:
            self.max_drawdown_percent = self.current_drawdown_percent

    def update_price(
        self,
        symbol: str,
        price: Decimal,
        *,
        timestamp: str | None = None,
        persist: bool = False,
    ) -> None:
        position = self.positions.get(symbol.upper())
        if position is not None:
            current = Decimal(str(price))
            position.current_price = current
            position.last_price_update = timestamp or utc_now_iso()
            position.price_stale = False
            position.highest_price_since_entry = max(
                position.highest_price_since_entry or position.entry_price,
                current,
            )
            self.refresh_metrics()
            if persist:
                self.save()

    def mark_to_market(self, price_provider, as_of: str | None = None) -> dict[str, bool]:
        """Refresh every open position; retain its last price on provider failure."""
        results: dict[str, bool] = {}
        valuation_date = as_of or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for symbol, position in list(self.positions.items()):
            try:
                market_price = price_provider.get_price_eur(symbol, valuation_date)
                self.update_price(
                    symbol,
                    market_price.price_eur,
                    timestamp=market_price.as_of or utc_now_iso(),
                )
                results[symbol] = True
            except Exception:  # noqa: BLE001 - stale prices must fail closed
                position.price_stale = True
                results[symbol] = False
        self.refresh_metrics()
        self.save()
        return results

    def daily_pnl(self, timestamp: str | None = None) -> Decimal:
        """Realized P&L, including fees, for the UTC calendar day."""
        day = (
            datetime.fromisoformat(timestamp).astimezone(timezone.utc).date()
            if timestamp
            else datetime.now(timezone.utc).date()
        )
        return sum(
            (
                trade.realized_pnl
                for trade in self.trade_history
                if datetime.fromisoformat(trade.timestamp).astimezone(timezone.utc).date() == day
            ),
            ZERO,
        )

    def apply_buy(
        self,
        *,
        symbol: str,
        action: PaperAction,
        quantity: Decimal,
        execution_price: Decimal,
        reference_price: Decimal,
        fees: Decimal,
        slippage: Decimal,
        timestamp: str,
        reason: str,
    ) -> TradeRecord:
        symbol = symbol.upper()
        gross_value = quantity * execution_price
        total_debit = gross_value + fees
        if action not in {PaperAction.BUY, PaperAction.INCREASE}:
            raise PortfolioError("INVALID_BUY_ACTION")
        if quantity <= ZERO:
            raise PortfolioError("INVALID_QUANTITY")
        if total_debit > self.cash_balance:
            raise PortfolioError("INSUFFICIENT_FUNDS")

        balance_before = self.cash_balance
        self.cash_balance -= total_debit
        existing = self.positions.get(symbol)
        if existing is None:
            if action is PaperAction.INCREASE:
                raise PortfolioError("POSITION_NOT_FOUND")
            self.positions[symbol] = Position(
                symbol=symbol,
                quantity=quantity,
                entry_price=execution_price,
                current_price=reference_price,
                invested_amount=gross_value,
                opened_at=timestamp,
                last_price_update=timestamp,
                price_stale=False,
                stop_loss_price=execution_price
                * (Decimal("1") - self.config.stop_loss_percent),
                take_profit_price=execution_price
                * (Decimal("1") + self.config.take_profit_percent),
                highest_price_since_entry=max(execution_price, reference_price),
            )
        else:
            if action is PaperAction.BUY:
                raise PortfolioError("POSITION_EXISTS")
            combined_quantity = existing.quantity + quantity
            existing.entry_price = (
                existing.quantity * existing.entry_price + gross_value
            ) / combined_quantity
            existing.quantity = combined_quantity
            existing.current_price = reference_price
            existing.last_price_update = timestamp
            existing.price_stale = False
            existing.stop_loss_price = existing.entry_price * (
                Decimal("1") - self.config.stop_loss_percent
            )
            existing.take_profit_price = existing.entry_price * (
                Decimal("1") + self.config.take_profit_percent
            )
            existing.highest_price_since_entry = max(
                existing.highest_price_since_entry or existing.entry_price,
                reference_price,
            )

        self.realized_pnl -= fees
        trade = TradeRecord(
            symbol=symbol,
            action=action,
            quantity=quantity,
            requested_price=reference_price,
            execution_price=execution_price,
            fees=fees,
            slippage=slippage,
            total_value=gross_value,
            timestamp=timestamp,
            balance_before=balance_before,
            balance_after=self.cash_balance,
            realized_pnl=-fees,
            reason=reason,
        )
        self.trade_history.append(trade)
        self.save()
        return trade

    def apply_sell(
        self,
        *,
        symbol: str,
        action: PaperAction,
        quantity: Decimal,
        execution_price: Decimal,
        reference_price: Decimal,
        fees: Decimal,
        slippage: Decimal,
        timestamp: str,
        reason: str,
    ) -> TradeRecord:
        symbol = symbol.upper()
        position = self.positions.get(symbol)
        if action not in {PaperAction.REDUCE, PaperAction.CLOSE}:
            raise PortfolioError("INVALID_SELL_ACTION")
        if position is None:
            raise PortfolioError("POSITION_NOT_FOUND")
        if quantity <= ZERO:
            raise PortfolioError("INVALID_QUANTITY")
        if quantity > position.quantity:
            raise PortfolioError("INSUFFICIENT_POSITION")

        balance_before = self.cash_balance
        gross_value = quantity * execution_price
        fees = min(fees, gross_value)
        proceeds_after_fees = gross_value - fees
        cost_basis = position.entry_price * quantity
        realized = proceeds_after_fees - cost_basis
        self.cash_balance += proceeds_after_fees
        self.realized_pnl += realized
        position.quantity -= quantity
        position.current_price = reference_price
        position.last_price_update = timestamp
        if position.quantity <= Decimal("0.000000000000000001"):
            del self.positions[symbol]

        trade = TradeRecord(
            symbol=symbol,
            action=action,
            quantity=quantity,
            requested_price=reference_price,
            execution_price=execution_price,
            fees=fees,
            slippage=slippage,
            total_value=gross_value,
            timestamp=timestamp,
            balance_before=balance_before,
            balance_after=self.cash_balance,
            realized_pnl=realized,
            reason=reason,
        )
        self.trade_history.append(trade)
        self.save()
        return trade

    def save_equity_snapshot(self, timestamp: str | None = None) -> bool:
        """Append one economically distinct snapshot; return whether it was added."""
        self.refresh_metrics()
        snapshot = EquitySnapshot(
            timestamp=timestamp or utc_now_iso(),
            cash=self.cash_balance,
            positions_value=self.total_exposure,
            total_equity=self.total_equity,
            realized_pnl=self.realized_pnl,
            unrealized_pnl=self.unrealized_pnl,
            total_exposure=self.total_exposure,
            drawdown_percent=self.current_drawdown_percent,
        )
        history = self.load_equity_history()
        comparable = snapshot.model_dump(mode="json", exclude={"timestamp"})
        if history:
            previous = {k: v for k, v in history[-1].items() if k != "timestamp"}
            if previous == comparable:
                return False
        history.append(snapshot.model_dump(mode="json"))
        self._atomic_json_write(self.equity_history_path, history)
        return True

    def load_equity_history(self) -> list[dict]:
        if not self.equity_history_path.exists():
            return []
        raw = json.loads(self.equity_history_path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, list) else []

    def snapshot(self) -> dict:
        self.refresh_metrics()
        return {
            "initial_balance": float(self.initial_balance),
            "cash_balance": float(self.cash_balance),
            "positions_value": float(self.total_exposure),
            "total_equity": float(self.total_equity),
            "realized_pnl": float(self.realized_pnl),
            "unrealized_pnl": float(self.unrealized_pnl),
            "total_pnl": float(self.total_pnl),
            "return_percent": float(self.return_percent),
            "total_exposure": float(self.total_exposure),
            "high_water_mark": float(self.high_water_mark),
            "current_drawdown_percent": float(self.current_drawdown_percent),
            "max_drawdown_percent": float(self.max_drawdown_percent),
            "positions": {
                symbol: {
                    **position.model_dump(mode="json"),
                    "market_value": float(position.market_value),
                    "return_percent": (
                        float(position.unrealized_pnl / position.invested_amount)
                        if position.invested_amount > ZERO
                        else 0.0
                    ),
                }
                for symbol, position in self.positions.items()
            },
            "trade_history": [trade.model_dump(mode="json") for trade in self.trade_history],
        }

    def _assert_invariants(self) -> None:
        if self.cash_balance < ZERO:
            raise PortfolioError("NEGATIVE_CASH_BALANCE")
        if any(position.quantity < ZERO for position in self.positions.values()):
            raise PortfolioError("NEGATIVE_POSITION")

