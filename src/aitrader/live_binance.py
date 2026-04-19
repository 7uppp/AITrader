from __future__ import annotations

from dataclasses import dataclass

import httpx

from .execution import SubmittedOrder
from .types import Side


@dataclass(slots=True)
class BinanceLiveAdapter:
    base_url: str
    api_key: str
    api_secret: str

    def submit_order(self, symbol: str, side: Side, quantity: float, price: float | None, reduce_only: bool) -> SubmittedOrder:
        raise NotImplementedError(
            "Live order routing is intentionally not implemented in the bootstrap. "
            "Integrate signed Binance endpoints with strict idempotency and dry-run gates first."
        )

    def cancel_all(self, symbol: str | None = None) -> None:
        _ = symbol
        raise NotImplementedError("Integrate Binance cancel-all with safe fallback retries before enabling live mode.")

    def ping(self) -> bool:
        with httpx.Client(timeout=3.0) as client:
            response = client.get(f"{self.base_url}/fapi/v1/time")
            return response.status_code == 200
