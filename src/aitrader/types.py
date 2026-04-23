from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Literal


class Side(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class SystemMode(str, Enum):
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    RISK_OFF = "RISK_OFF"
    KILLED = "KILLED"


class LotKind(str, Enum):
    MAIN = "MAIN"
    RUNNER = "RUNNER"


class OrderState(str, Enum):
    INTENT_CREATED = "INTENT_CREATED"
    RISK_APPROVED = "RISK_APPROVED"
    SUBMIT_PENDING = "SUBMIT_PENDING"
    ACKED = "ACKED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    EXIT_MANAGED = "EXIT_MANAGED"
    CLOSED = "CLOSED"
    REJECTED = "REJECTED"
    RECOVERED = "RECOVERED"


@dataclass(slots=True)
class Candle:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(slots=True)
class MarketSnapshot:
    symbol: str
    ts: datetime
    candles_4h: list[Candle]
    candles_1h: list[Candle]
    candles_15m: list[Candle]
    mark_price: float
    index_price: float
    funding_rate_pct: float
    oi_change_1h_pct: float
    long_short_ratio: float
    bid_ask_spread_bps: float
    estimated_slippage_bps: float
    atr_1h_percentile: float
    is_stale: bool = False
    risk_extreme: bool = False


@dataclass(slots=True)
class SignalIntent:
    symbol: str
    side: Side
    entry_price: float
    initial_stop: float
    confidence: float
    reason_codes: list[str] = field(default_factory=list)

    @property
    def risk_distance(self) -> float:
        return abs(self.entry_price - self.initial_stop)


@dataclass(slots=True)
class RiskDecision:
    approved: bool
    reason_codes: list[str]
    quantity: float = 0.0
    mode_override: SystemMode | None = None
    liquidation_price: float | None = None
    liquidation_buffer_pct: float | None = None


@dataclass(slots=True)
class AccountState:
    equity: float
    free_margin: float
    daily_pnl_pct: float
    weekly_pnl_pct: float
    drawdown_pct: float
    consecutive_losses: int
    open_positions: int
    open_risk_pct: float
    symbol_notional_pct: float

    @property
    def free_margin_pct(self) -> float:
        if self.equity <= 0:
            return 0.0
        return (self.free_margin / self.equity) * 100.0


@dataclass(slots=True)
class PositionLot:
    symbol: str
    side: Side
    kind: LotKind
    quantity: float
    avg_entry: float
    initial_stop: float
    current_stop: float
    one_r_value: float
    take_profit: float | None = None
    active: bool = True
    trailing_armed: bool = False
    breakeven_armed: bool = False
    realized_pnl: float = 0.0
    opened_at: datetime | None = None
    entry_timeframe: str = "1h_primary"
    bars_held_15m: int = 0
    bars_held_1h: int = 0
    last_signal_state: str = ""
    exit_reason: str = ""
    exit_executed: bool = False
    closed_at: datetime | None = None


ControlAction = Literal[
    "NOOP",
    "STATUS",
    "POSITIONS",
    "PNL",
    "PAUSE",
    "RESUME",
    "CLOSE_ALL",
    "KILL_SWITCH",
    "RISK_OFF",
]
