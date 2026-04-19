from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .config import StrategyConfig, TradingConfig
from .indicators import atr, ema
from .types import MarketSnapshot, Side, SignalIntent

TimeframeMode = Literal["15m", "1h", "hybrid"]


@dataclass(slots=True)
class SignalEvaluation:
    signal: SignalIntent | None
    failed_reasons: list[str]


@dataclass(slots=True)
class SignalEngine:
    trading: TradingConfig
    strategy: StrategyConfig

    def evaluate(self, snapshot: MarketSnapshot) -> SignalIntent | None:
        return self.evaluate_explain(snapshot, timeframe_mode="hybrid").signal

    def evaluate_explain(self, snapshot: MarketSnapshot, timeframe_mode: TimeframeMode = "hybrid") -> SignalEvaluation:
        failed: list[str] = []
        if snapshot.is_stale:
            failed.append("market_stale")
        if len(snapshot.candles_4h) < 210 or len(snapshot.candles_1h) < 60 or len(snapshot.candles_15m) < 40:
            failed.append("insufficient_history")
        if timeframe_mode == "15m":
            atr_ok = 0.20 <= snapshot.atr_1h_percentile <= 0.90
        elif timeframe_mode == "1h":
            atr_ok = 0.25 <= snapshot.atr_1h_percentile <= 0.80
        else:
            atr_ok = 0.30 <= snapshot.atr_1h_percentile <= 0.75
        if not atr_ok:
            failed.append("atr_percentile_out_of_range")
        if failed:
            return SignalEvaluation(signal=None, failed_reasons=failed)

        if timeframe_mode == "15m":
            side = self._trend_side_1h(snapshot)
        else:
            side = self._trend_side_4h(snapshot)
        if side is None:
            failed.append("trend_not_confirmed")
        if side == Side.LONG and not self.trading.allow_long:
            failed.append("long_disabled")
        if side == Side.SHORT and not self.trading.allow_short:
            failed.append("short_disabled")
        if failed:
            return SignalEvaluation(signal=None, failed_reasons=failed)

        if timeframe_mode == "15m":
            pullback_ok = self._pullback_valid_15m(snapshot, side)
        else:
            pullback_ok = self._pullback_valid_1h(snapshot, side)
        if not pullback_ok:
            failed.append("pullback_not_confirmed")
        if timeframe_mode == "1h":
            breakout_ok = self._breakout_valid_1h(snapshot, side)
        elif timeframe_mode == "15m":
            breakout_ok = self._breakout_valid_15m(snapshot, side, lookback=6, volume_multiplier=1.0)
        else:
            breakout_ok = self._breakout_valid_15m(snapshot, side, lookback=self.strategy.breakout_lookback, volume_multiplier=self.strategy.volume_multiplier)
        if not breakout_ok:
            failed.append("breakout_or_volume_not_confirmed")
        if failed:
            return SignalEvaluation(signal=None, failed_reasons=failed)

        entry = snapshot.mark_price
        atr_15 = atr(snapshot.candles_15m, period=14)
        if atr_15 <= 0:
            return SignalEvaluation(signal=None, failed_reasons=["atr_invalid"])
        structure_low = min(c.low for c in snapshot.candles_1h[-20:])
        structure_high = max(c.high for c in snapshot.candles_1h[-20:])
        if side == Side.LONG:
            structure_stop = structure_low - 0.2 * atr_15
            vol_stop = entry - 1.3 * atr_15
            initial_stop = min(structure_stop, vol_stop)
        else:
            structure_stop = structure_high + 0.2 * atr_15
            vol_stop = entry + 1.3 * atr_15
            initial_stop = max(structure_stop, vol_stop)
        if abs(entry - initial_stop) <= 0:
            return SignalEvaluation(signal=None, failed_reasons=["risk_distance_invalid"])

        reasons = [
            f"trend:{side.value.lower()}",
            "pullback:confirmed",
            "breakout:confirmed",
            "volume:confirmed",
        ]
        signal = SignalIntent(
            symbol=snapshot.symbol,
            side=side,
            entry_price=entry,
            initial_stop=initial_stop,
            confidence=0.65,
            reason_codes=[*reasons, f"timeframe:{timeframe_mode}"],
        )
        return SignalEvaluation(signal=signal, failed_reasons=[])

    def _trend_side_4h(self, snapshot: MarketSnapshot) -> Side | None:
        closes_4h = [c.close for c in snapshot.candles_4h]
        ema50 = ema(closes_4h, 50)
        ema200 = ema(closes_4h, 200)
        if len(ema50) < 6 or len(ema200) < 2:
            return None
        slope_up = all(ema50[-i] > ema50[-i - 1] for i in range(1, 6))
        slope_down = all(ema50[-i] < ema50[-i - 1] for i in range(1, 6))
        above = all(c.close > ema50[-2 + idx] for idx, c in enumerate(snapshot.candles_4h[-2:]))
        below = all(c.close < ema50[-2 + idx] for idx, c in enumerate(snapshot.candles_4h[-2:]))

        if ema50[-1] > ema200[-1] and slope_up and above:
            return Side.LONG
        if ema50[-1] < ema200[-1] and slope_down and below:
            return Side.SHORT
        return None

    def _trend_side_1h(self, snapshot: MarketSnapshot) -> Side | None:
        closes = [c.close for c in snapshot.candles_1h]
        ema50 = ema(closes, 50)
        ema200 = ema(closes, 200)
        if len(ema50) < 4 or len(ema200) < 2:
            return None
        slope_up = all(ema50[-i] > ema50[-i - 1] for i in range(1, 4))
        slope_down = all(ema50[-i] < ema50[-i - 1] for i in range(1, 4))
        if ema50[-1] > ema200[-1] and slope_up:
            return Side.LONG
        if ema50[-1] < ema200[-1] and slope_down:
            return Side.SHORT
        return None

    def _pullback_valid_1h(self, snapshot: MarketSnapshot, side: Side) -> bool:
        closes_1h = [c.close for c in snapshot.candles_1h]
        ema20 = ema(closes_1h, 20)[-1]
        ema50 = ema(closes_1h, 50)[-1]
        last_close = closes_1h[-1]
        structure_low = min(c.low for c in snapshot.candles_1h[-12:])
        structure_high = max(c.high for c in snapshot.candles_1h[-12:])

        if side == Side.LONG:
            in_band = min(ema20, ema50) <= last_close <= max(ema20, ema50) * 1.01
            return in_band and last_close > structure_low
        in_band = min(ema20, ema50) * 0.99 <= last_close <= max(ema20, ema50)
        return in_band and last_close < structure_high

    def _pullback_valid_15m(self, snapshot: MarketSnapshot, side: Side) -> bool:
        closes = [c.close for c in snapshot.candles_15m]
        ema20 = ema(closes, 20)[-1]
        ema50 = ema(closes, 50)[-1]
        last_close = closes[-1]
        structure_low = min(c.low for c in snapshot.candles_15m[-16:])
        structure_high = max(c.high for c in snapshot.candles_15m[-16:])
        if side == Side.LONG:
            in_band = min(ema20, ema50) <= last_close <= max(ema20, ema50) * 1.01
            return in_band and last_close > structure_low
        in_band = min(ema20, ema50) * 0.99 <= last_close <= max(ema20, ema50)
        return in_band and last_close < structure_high

    def _breakout_valid_15m(self, snapshot: MarketSnapshot, side: Side, lookback: int, volume_multiplier: float) -> bool:
        if len(snapshot.candles_15m) < max(lookback + 1, 21):
            return False
        current = snapshot.candles_15m[-1]
        window = snapshot.candles_15m[-(lookback + 1) : -1]
        avg_vol = sum(c.volume for c in snapshot.candles_15m[-21:-1]) / 20.0
        volume_ok = current.volume >= avg_vol * volume_multiplier
        if not volume_ok:
            return False
        if side == Side.LONG:
            breakout = current.close > max(c.high for c in window)
        else:
            breakout = current.close < min(c.low for c in window)
        return breakout

    def _breakout_valid_1h(self, snapshot: MarketSnapshot, side: Side) -> bool:
        lookback = 8
        if len(snapshot.candles_1h) < max(lookback + 1, 21):
            return False
        current = snapshot.candles_1h[-1]
        window = snapshot.candles_1h[-(lookback + 1) : -1]
        avg_vol = sum(c.volume for c in snapshot.candles_1h[-21:-1]) / 20.0
        volume_ok = current.volume >= avg_vol * 1.1
        if not volume_ok:
            return False
        if side == Side.LONG:
            return current.close > max(c.high for c in window)
        return current.close < min(c.low for c in window)
