from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import uuid4

from .time_utils import utc_now
from .types import Side


@dataclass(slots=True)
class SubmittedOrder:
    client_order_id: str
    symbol: str
    side: Side
    quantity: float
    price: float | None
    reduce_only: bool
    created_at: datetime
    status: str = "FILLED"


class ExecutionAdapter(Protocol):
    def submit_order(self, symbol: str, side: Side, quantity: float, price: float | None, reduce_only: bool) -> SubmittedOrder:
        ...

    def cancel_all(self, symbol: str | None = None) -> None:
        ...


@dataclass(slots=True)
class PaperExecutionAdapter:
    orders: list[SubmittedOrder] = field(default_factory=list)

    def submit_order(self, symbol: str, side: Side, quantity: float, price: float | None, reduce_only: bool) -> SubmittedOrder:
        order = SubmittedOrder(
            client_order_id=f"paper-{uuid4().hex[:16]}",
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            reduce_only=reduce_only,
            created_at=utc_now(),
            status="FILLED",
        )
        self.orders.append(order)
        return order

    def cancel_all(self, symbol: str | None = None) -> None:
        if symbol is None:
            self.orders = []
            return
        self.orders = [order for order in self.orders if order.symbol != symbol]


@dataclass(slots=True)
class ExecutionEngine:
    adapter: ExecutionAdapter
    idempotency_cache: set[str] = field(default_factory=set)

    def place(self, request_id: str, symbol: str, side: Side, quantity: float, price: float | None, reduce_only: bool = False) -> SubmittedOrder | None:
        if request_id in self.idempotency_cache:
            return None
        self.idempotency_cache.add(request_id)
        return self.adapter.submit_order(symbol=symbol, side=side, quantity=quantity, price=price, reduce_only=reduce_only)

    def close_all(self, symbols: list[str]) -> None:
        for symbol in symbols:
            self.adapter.cancel_all(symbol=symbol)
