from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .config import RiskConfig, StrategyConfig
from .indicators import ema
from .time_utils import utc_now
from .types import MarketSnapshot
from .types import LotKind, PositionLot, Side, SignalIntent


@dataclass(slots=True)
class PositionManager:
    strategy: StrategyConfig
    risk: RiskConfig
    lots: list[PositionLot] = field(default_factory=list)

    def open_split_position(
        self,
        signal: SignalIntent,
        quantity: float,
        opened_at: datetime | None = None,
        entry_timeframe: str = "1h_primary",
        last_signal_state: str = "",
        advice_id: str = "",
    ) -> list[PositionLot]:
        if quantity <= 0:
            return []
        one_r = signal.risk_distance
        opened = opened_at or utc_now()
        main_qty = quantity * self.strategy.main_lot_ratio
        runner_qty = quantity * self.strategy.runner_lot_ratio
        if signal.side == Side.LONG:
            main_tp = signal.entry_price + one_r
        else:
            main_tp = signal.entry_price - one_r
        main = PositionLot(
            symbol=signal.symbol,
            side=signal.side,
            kind=LotKind.MAIN,
            quantity=main_qty,
            avg_entry=signal.entry_price,
            initial_stop=signal.initial_stop,
            current_stop=signal.initial_stop,
            one_r_value=one_r,
            take_profit=main_tp,
            opened_at=opened,
            entry_timeframe=entry_timeframe,
            last_signal_state=last_signal_state,
            advice_id=advice_id,
        )
        runner = PositionLot(
            symbol=signal.symbol,
            side=signal.side,
            kind=LotKind.RUNNER,
            quantity=runner_qty,
            avg_entry=signal.entry_price,
            initial_stop=signal.initial_stop,
            current_stop=signal.initial_stop,
            one_r_value=one_r,
            take_profit=None,
            opened_at=opened,
            entry_timeframe=entry_timeframe,
            last_signal_state=last_signal_state,
            advice_id=advice_id,
        )
        self.lots.extend([main, runner])
        return [main, runner]

    def update(self, snapshot: MarketSnapshot, atr_15m: float, is_15m_close: bool, risk_extreme: bool = False) -> list[str]:
        events: list[str] = []
        symbol = snapshot.symbol
        grouped = [lot for lot in self.lots if lot.symbol == symbol and lot.active]
        if not grouped:
            return events

        now = snapshot.ts
        last_price = snapshot.mark_price
        closes_1h = [c.close for c in snapshot.candles_1h]
        closes_15m = [c.close for c in snapshot.candles_15m]
        ema_fast_1h = ema(closes_1h, self.strategy.ema_fast_period) if closes_1h else []
        ema_mid_1h = ema(closes_1h, self.strategy.ema_mid_period) if closes_1h else []
        ema_fast_15m = ema(closes_15m, self.strategy.ema_fast_period) if closes_15m else []
        current_1h_close = closes_1h[-1] if closes_1h else last_price
        current_15m_close = closes_15m[-1] if closes_15m else last_price

        for lot in grouped:
            self._refresh_lot_age(lot, now)

        main = next((lot for lot in grouped if lot.kind == LotKind.MAIN), None)
        runner = next((lot for lot in grouped if lot.kind == LotKind.RUNNER), None)

        if main and self._is_tp_hit(main, last_price):
            self._deactivate_lot(main, "main:tp_hit", main.take_profit if main.take_profit is not None else last_price, now)
            events.append("main:tp_hit")
            if runner and runner.active and not runner.breakeven_armed:
                runner.current_stop = self._breakeven_stop(runner)
                runner.breakeven_armed = True
                events.append("runner:breakeven_armed")

        if main and main.active and self._is_stop_hit(main, last_price):
            self._deactivate_lot(main, "main:stop_hit", main.current_stop, now)
            events.append("main:stop_hit")

        if runner and runner.active:
            if not runner.trailing_armed and self._reached_r_multiple(runner, last_price, self.strategy.runner_trailing_activation_r):
                runner.trailing_armed = True
                events.append("runner:trailing_armed")

            if runner.trailing_armed and is_15m_close:
                atr_mult = self.strategy.runner_trailing_atr_mult_tight if (risk_extreme and self.strategy.risk_extreme_mode_tighten_trailing) else self.strategy.runner_trailing_atr_mult
                if runner.side == Side.LONG:
                    new_stop = max(runner.current_stop, last_price - atr_mult * atr_15m)
                else:
                    new_stop = min(runner.current_stop, last_price + atr_mult * atr_15m)
                if new_stop != runner.current_stop:
                    runner.current_stop = new_stop
                    events.append("runner:trailing_updated")

            if self._is_stop_hit(runner, last_price):
                self._deactivate_lot(runner, "runner:stop_hit", runner.current_stop, now)
                events.append("runner:stop_hit")

        if grouped and self._structure_invalidated(
            side=grouped[0].side,
            current_1h_close=current_1h_close,
            current_15m_close=current_15m_close,
            ema_fast_1h=ema_fast_1h,
            ema_mid_1h=ema_mid_1h,
            ema_fast_15m=ema_fast_15m,
        ):
            for lot in grouped:
                if lot.active:
                    self._deactivate_lot(lot, "structure_invalidated", last_price, now)
                    events.append(f"{lot.kind.value.lower()}:structure_invalidated")
            return events

        soft_minutes, hard_minutes = self._time_stop_thresholds(grouped[0].entry_timeframe)
        elapsed_minutes = self._elapsed_minutes(grouped[0], now)
        stalled = self._is_stalled(grouped[0], last_price, snapshot.atr_1h_percentile)

        if elapsed_minutes >= hard_minutes or (stalled and elapsed_minutes >= hard_minutes):
            for lot in grouped:
                if lot.active:
                    self._deactivate_lot(lot, "time_stop_hard", last_price, now)
                    events.append(f"{lot.kind.value.lower()}:time_stop_hard")
            return events

        if elapsed_minutes >= soft_minutes and stalled and runner and runner.active:
            self._deactivate_lot(runner, "time_stop_soft", last_price, now)
            events.append("runner:time_stop_soft")

        return events

    def close_all(self) -> list[PositionLot]:
        closed: list[PositionLot] = []
        for lot in self.lots:
            if lot.active:
                self._deactivate_lot(lot, "manual_close_all", lot.current_stop, utc_now())
                closed.append(lot)
        return closed

    def _reached_r_multiple(self, lot: PositionLot, price: float, r_mult: float) -> bool:
        if lot.side == Side.LONG:
            return price >= lot.avg_entry + (lot.one_r_value * r_mult)
        return price <= lot.avg_entry - (lot.one_r_value * r_mult)

    def _breakeven_stop(self, lot: PositionLot) -> float:
        buffer_bps = self.risk.fee_buffer_bps + self.risk.slippage_buffer_bps + self.risk.tick_buffer_bps
        buffer = lot.avg_entry * (buffer_bps / 10_000.0)
        if lot.side == Side.LONG:
            return lot.avg_entry + buffer
        return lot.avg_entry - buffer

    @staticmethod
    def _is_tp_hit(lot: PositionLot, price: float) -> bool:
        if lot.take_profit is None:
            return False
        if lot.side == Side.LONG:
            return price >= lot.take_profit
        return price <= lot.take_profit

    @staticmethod
    def _is_stop_hit(lot: PositionLot, price: float) -> bool:
        if lot.side == Side.LONG:
            return price <= lot.current_stop
        return price >= lot.current_stop

    @staticmethod
    def _pnl(lot: PositionLot, exit_price: float) -> float:
        if lot.side == Side.LONG:
            return (exit_price - lot.avg_entry) * lot.quantity
        return (lot.avg_entry - exit_price) * lot.quantity

    @staticmethod
    def _elapsed_minutes(lot: PositionLot, now: datetime) -> float:
        if lot.opened_at is None:
            return 0.0
        delta = now - lot.opened_at
        return max(0.0, delta.total_seconds() / 60.0)

    @staticmethod
    def _time_stop_thresholds(entry_timeframe: str) -> tuple[int, int]:
        if entry_timeframe.strip().lower() == "15m":
            return (45, 60)
        return (90, 120)

    def _refresh_lot_age(self, lot: PositionLot, now: datetime) -> None:
        if lot.opened_at is None:
            return
        elapsed = now - lot.opened_at
        elapsed_seconds = max(0.0, elapsed.total_seconds())
        lot.bars_held_15m = max(lot.bars_held_15m, int(elapsed_seconds // (15 * 60)))
        lot.bars_held_1h = max(lot.bars_held_1h, int(elapsed_seconds // (60 * 60)))

    def _progress_r(self, lot: PositionLot, price: float) -> float:
        if lot.one_r_value <= 0:
            return 0.0
        if lot.side == Side.LONG:
            return (price - lot.avg_entry) / lot.one_r_value
        return (lot.avg_entry - price) / lot.one_r_value

    def _is_stalled(self, lot: PositionLot, price: float, atr_1h_percentile: float) -> bool:
        return self._progress_r(lot, price) < self.strategy.stalled_progress_r_threshold and atr_1h_percentile <= self.strategy.stalled_atr_percentile_max

    @staticmethod
    def _structure_invalidated(
        side: Side,
        current_1h_close: float,
        current_15m_close: float,
        ema_fast_1h: list[float],
        ema_mid_1h: list[float],
        ema_fast_15m: list[float],
    ) -> bool:
        if not ema_fast_1h or not ema_mid_1h or not ema_fast_15m:
            return False
        if side == Side.LONG:
            return current_1h_close < ema_mid_1h[-1] and current_15m_close < ema_fast_15m[-1]
        return current_1h_close > ema_mid_1h[-1] and current_15m_close > ema_fast_15m[-1]

    def _deactivate_lot(self, lot: PositionLot, reason: str, exit_price: float, now: datetime) -> None:
        if not lot.active:
            return
        lot.active = False
        lot.exit_reason = reason
        lot.closed_at = now
        lot.realized_pnl = self._pnl(lot, exit_price)
