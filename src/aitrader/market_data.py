from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .types import MarketSnapshot


@dataclass(slots=True)
class MarketDataPolicy:
    stale_after_seconds: int = 20
    max_spread_bps: float = 20.0
    max_slippage_bps: float = 30.0


@dataclass(slots=True)
class MarketDataValidator:
    policy: MarketDataPolicy

    def validate(self, snapshot: MarketSnapshot, now: datetime) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        if snapshot.ts < now - timedelta(seconds=self.policy.stale_after_seconds):
            reasons.append("stale_snapshot")
        if snapshot.bid_ask_spread_bps > self.policy.max_spread_bps:
            reasons.append("spread_too_wide")
        if snapshot.estimated_slippage_bps > self.policy.max_slippage_bps:
            reasons.append("slippage_too_high")
        return (len(reasons) == 0, reasons)
