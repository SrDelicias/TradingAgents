"""Reference prices sourced only through the project's existing Yahoo data path."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

import yfinance as yf

from tradingagents.dataflows.stockstats_utils import load_ohlcv
from tradingagents.dataflows.symbol_utils import normalize_symbol

from .models import MarketPrice, utc_now_iso


class PriceUnavailableError(RuntimeError):
    pass


class MarketPriceService:
    """Return the latest settled close at or before the analysis date, in EUR."""

    def get_price_eur(self, symbol: str, as_of: str) -> MarketPrice:
        canonical = normalize_symbol(symbol)
        try:
            frame = load_ohlcv(canonical, as_of)
            source_price = Decimal(str(frame.iloc[-1]["Close"]))
        except Exception as exc:  # noqa: BLE001 - fail closed at the execution boundary
            raise PriceUnavailableError("PRICE_UNAVAILABLE") from exc
        if not source_price.is_finite() or source_price <= 0:
            raise PriceUnavailableError("PRICE_UNAVAILABLE")

        currency = self._currency(canonical)
        if currency == "GBp":
            source_price /= Decimal("100")
            currency = "GBP"

        fx_rate = Decimal("1")
        if currency != "EUR":
            try:
                fx_frame = load_ohlcv(f"{currency}EUR=X", as_of)
                fx_rate = Decimal(str(fx_frame.iloc[-1]["Close"]))
            except (Exception, InvalidOperation) as exc:  # noqa: BLE001
                raise PriceUnavailableError("PRICE_UNAVAILABLE") from exc
            if not fx_rate.is_finite() or fx_rate <= 0:
                raise PriceUnavailableError("PRICE_UNAVAILABLE")

        return MarketPrice(
            symbol=symbol.upper(),
            price_eur=source_price * fx_rate,
            source_price=source_price,
            source_currency=currency,
            fx_rate_to_eur=fx_rate,
            as_of=as_of or utc_now_iso(),
        )

    @staticmethod
    def _currency(canonical: str) -> str:
        """Use Yahoo metadata, with deterministic suffix fallbacks."""
        try:
            currency = yf.Ticker(canonical).fast_info.get("currency")
            if currency:
                return str(currency)
        except Exception:  # noqa: BLE001 - suffix inference is the safe fallback
            pass

        suffixes = {
            "-USD": "USD",
            ".HK": "HKD",
            ".T": "JPY",
            ".L": "GBP",
            ".TO": "CAD",
            ".AX": "AUD",
            ".NS": "INR",
            ".BO": "INR",
            ".SS": "CNY",
            ".SZ": "CNY",
            ".DE": "EUR",
            ".PA": "EUR",
            ".AS": "EUR",
            ".MC": "EUR",
            ".MI": "EUR",
        }
        upper = canonical.upper()
        for suffix, currency in suffixes.items():
            if upper.endswith(suffix):
                return currency
        return "USD"

