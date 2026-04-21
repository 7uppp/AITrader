from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType
from typing import Any
from uuid import uuid4

from .config import HyperliquidConfig
from .execution import SubmittedOrder
from .time_utils import utc_now
from .types import Side


def _to_hl_coin(symbol: str) -> str:
    token = symbol.upper().strip()
    if token.endswith("USDT"):
        return token[:-4]
    return token


@dataclass(slots=True)
class HyperliquidLiveAdapter:
    config: HyperliquidConfig
    dry_run: bool = True
    _sdk_loaded: bool = False
    _sdk_error: str | None = None
    _exchange: Any = None
    _info: Any = None
    _account: Any = None
    _eth_account_module: ModuleType | None = None

    def submit_order(self, symbol: str, side: Side, quantity: float, price: float | None, reduce_only: bool) -> SubmittedOrder:
        created_at = utc_now()
        client_order_id = f"hl-{uuid4().hex[:16]}"
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.dry_run:
            return SubmittedOrder(
                client_order_id=client_order_id,
                symbol=symbol,
                side=side,
                quantity=quantity,
                price=price,
                reduce_only=reduce_only,
                created_at=created_at,
                status="DRY_RUN",
            )
        self._ensure_ready()
        assert self._exchange is not None

        coin = _to_hl_coin(symbol)
        is_buy = side == Side.LONG
        order_resp: object
        if price is None:
            order_resp = self._submit_market_like_order(
                coin=coin,
                is_buy=is_buy,
                quantity=quantity,
                reduce_only=reduce_only,
            )
        else:
            order_resp = self._submit_limit_order(
                coin=coin,
                is_buy=is_buy,
                quantity=quantity,
                limit_price=price,
                reduce_only=reduce_only,
            )

        status = "SUBMITTED"
        if isinstance(order_resp, dict):
            status = str(order_resp.get("status", "SUBMITTED"))
        return SubmittedOrder(
            client_order_id=client_order_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            reduce_only=reduce_only,
            created_at=created_at,
            status=status,
        )

    def cancel_all(self, symbol: str | None = None) -> None:
        if self.dry_run:
            return
        self._ensure_ready()
        assert self._exchange is not None
        coin = _to_hl_coin(symbol) if symbol else None
        if hasattr(self._exchange, "cancel_all_orders"):
            if coin is None:
                self._exchange.cancel_all_orders()
            else:
                self._exchange.cancel_all_orders(coin)
            return
        if hasattr(self._exchange, "cancel"):
            if coin is None:
                return
            self._exchange.cancel(coin)
            return
        raise RuntimeError("hyperliquid sdk missing cancel method")

    def submit_protection_orders(
        self,
        symbol: str,
        side: Side,
        quantity: float,
        stop_price: float,
        main_tp_price: float,
    ) -> dict[str, object]:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.dry_run:
            return {"status": "DRY_RUN", "symbol": symbol}

        self._ensure_ready()
        assert self._exchange is not None
        coin = _to_hl_coin(symbol)
        is_buy_close = side == Side.SHORT
        stop_order_type = {"trigger": {"triggerPx": stop_price, "isMarket": True, "tpsl": "sl"}}
        tp_order_type = {"trigger": {"triggerPx": main_tp_price, "isMarket": True, "tpsl": "tp"}}
        stop = self._exchange.order(
            coin,
            is_buy_close,
            quantity,
            stop_price,
            stop_order_type,
            reduce_only=True,
            cloid=f"aitrader-sl-{uuid4().hex[:18]}",
        )
        tp = self._exchange.order(
            coin,
            is_buy_close,
            quantity * 0.6,
            main_tp_price,
            tp_order_type,
            reduce_only=True,
            cloid=f"aitrader-tp-{uuid4().hex[:18]}",
        )
        return {"status": "SUBMITTED", "stop": stop, "tp": tp}

    def _submit_limit_order(self, coin: str, is_buy: bool, quantity: float, limit_price: float, reduce_only: bool) -> object:
        assert self._exchange is not None
        order_type = {"limit": {"tif": "Gtc"}}
        cloid = f"aitrader-{uuid4().hex[:20]}"
        return self._exchange.order(coin, is_buy, quantity, limit_price, order_type, reduce_only=reduce_only, cloid=cloid)

    def _submit_market_like_order(self, coin: str, is_buy: bool, quantity: float, reduce_only: bool) -> object:
        assert self._exchange is not None
        if hasattr(self._exchange, "market_open") and not reduce_only:
            return self._exchange.market_open(coin, is_buy, quantity, None)
        if hasattr(self._exchange, "market_close") and reduce_only:
            return self._exchange.market_close(coin)

        mid = self._mid_price(coin)
        if mid <= 0:
            raise RuntimeError(f"failed to get mid price for {coin}")
        limit_price = mid * (1.002 if is_buy else 0.998)
        order_type = {"limit": {"tif": "Ioc"}}
        cloid = f"aitrader-{uuid4().hex[:20]}"
        return self._exchange.order(coin, is_buy, quantity, limit_price, order_type, reduce_only=reduce_only, cloid=cloid)

    def _mid_price(self, coin: str) -> float:
        assert self._info is not None
        mids = self._info.all_mids()
        try:
            return float(mids.get(coin, 0.0))
        except Exception:
            return 0.0

    def _ensure_ready(self) -> None:
        if self._exchange is not None and self._info is not None:
            return
        if not self.config.private_key.strip():
            raise RuntimeError("AITRADER_HL_PRIVATE_KEY is required for live trading")
        self._load_sdk()
        if self._sdk_error:
            raise RuntimeError(self._sdk_error)
        assert self._eth_account_module is not None
        account = self._eth_account_module.Account.from_key(self.config.private_key.strip())
        self._account = account
        exchange_cls = self._load_attr("hyperliquid.exchange", "Exchange")
        info_cls = self._load_attr("hyperliquid.info", "Info")
        account_address = self.config.vault_address.strip() or None
        self._info = info_cls(self.config.api_url, skip_ws=True)
        try:
            self._exchange = exchange_cls(account, self.config.api_url, account_address=account_address)
        except TypeError:
            if account_address:
                self._exchange = exchange_cls(account, self.config.api_url, account_address)
            else:
                self._exchange = exchange_cls(account, self.config.api_url)

    def _load_sdk(self) -> None:
        if self._sdk_loaded:
            return
        self._sdk_loaded = True
        try:
            import importlib

            self._eth_account_module = importlib.import_module("eth_account")
            importlib.import_module("hyperliquid.exchange")
            importlib.import_module("hyperliquid.info")
            self._sdk_error = None
        except Exception as exc:
            self._sdk_error = (
                "hyperliquid sdk not available. Install with "
                "'pip install hyperliquid-python-sdk eth-account' "
                f"({type(exc).__name__})."
            )

    @staticmethod
    def _load_attr(module_name: str, attr_name: str) -> Any:
        import importlib

        module = importlib.import_module(module_name)
        return getattr(module, attr_name)
