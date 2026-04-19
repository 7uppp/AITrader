from __future__ import annotations

from dataclasses import dataclass, field

from .config import RiskConfig, StrategyConfig
from .types import LotKind, PositionLot, Side, SignalIntent


@dataclass(slots=True)
class PositionManager:
    strategy: StrategyConfig
    risk: RiskConfig
    lots: list[PositionLot] = field(default_factory=list)

    def open_split_position(self, signal: SignalIntent, quantity: float) -> list[PositionLot]:
        if quantity <= 0:
            return []
        one_r = signal.risk_distance
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
        )
        self.lots.extend([main, runner])
        return [main, runner]

    def update(self, symbol: str, last_price: float, atr_15m: float, is_15m_close: bool, risk_extreme: bool = False) -> list[str]:
        events: list[str] = []
        grouped = [lot for lot in self.lots if lot.symbol == symbol and lot.active]
        if not grouped:
            return events

        main = next((lot for lot in grouped if lot.kind == LotKind.MAIN), None)
        runner = next((lot for lot in grouped if lot.kind == LotKind.RUNNER), None)

        if main and self._is_tp_hit(main, last_price):
            main.active = False
            main.realized_pnl = self._pnl(main, main.take_profit if main.take_profit is not None else last_price)
            events.append("main:tp_hit")
            if runner and runner.active and not runner.breakeven_armed:
                runner.current_stop = self._breakeven_stop(runner)
                runner.breakeven_armed = True
                events.append("runner:breakeven_armed")

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
                runner.active = False
                runner.realized_pnl = self._pnl(runner, runner.current_stop)
                events.append("runner:stop_hit")

        return events

    def close_all(self) -> list[PositionLot]:
        closed: list[PositionLot] = []
        for lot in self.lots:
            if lot.active:
                lot.active = False
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
