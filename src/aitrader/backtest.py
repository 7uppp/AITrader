from __future__ import annotations

from dataclasses import dataclass, field

from .position_manager import PositionManager
from .risk import RiskEngine
from .strategy import SignalEngine
from .types import AccountState, MarketSnapshot, SystemMode


@dataclass(slots=True)
class BacktestResult:
    events: list[str] = field(default_factory=list)
    trades: int = 0
    approved_signals: int = 0
    rejected_signals: int = 0


@dataclass(slots=True)
class Backtester:
    signal_engine: SignalEngine
    risk_engine: RiskEngine
    position_manager: PositionManager
    mode: SystemMode = SystemMode.RUNNING

    def run(self, snapshots: list[MarketSnapshot], account: AccountState) -> BacktestResult:
        result = BacktestResult()
        for idx, snapshot in enumerate(snapshots):
            signal = self.signal_engine.evaluate(snapshot)
            if signal is None:
                continue
            decision = self.risk_engine.assess(signal, snapshot, account, self.mode)
            if not decision.approved:
                result.rejected_signals += 1
                result.events.append(f"{snapshot.ts.isoformat()} reject:{','.join(decision.reason_codes)}")
                continue
            result.approved_signals += 1
            lots = self.position_manager.open_split_position(signal, decision.quantity)
            if lots:
                result.trades += 1
                result.events.append(f"{snapshot.ts.isoformat()} open:{signal.symbol}:{signal.side.value}:{decision.quantity:.6f}")
            events = self.position_manager.update(
                snapshot=snapshot,
                atr_15m=0.0,
                is_15m_close=(idx % 1 == 0),
                risk_extreme=snapshot.risk_extreme,
            )
            result.events.extend(events)
        return result
