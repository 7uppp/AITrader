from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .config import StrategyConfig, TradingConfig
from .indicators import atr, bollinger_bands, ema, rolling_high, rolling_low, rsi, sma
from .types import MarketSnapshot, Side, SignalIntent

TimeframeMode = Literal["15m", "1h", "hybrid", "1h_primary"]


@dataclass(slots=True)
class SignalEvaluation:
    signal: SignalIntent | None
    failed_reasons: list[str]


@dataclass(slots=True)
class TriggerEvaluation:
    ok: bool
    trigger_code: str
    confirmation_codes: list[str]


@dataclass(slots=True)
class SignalEngine:
    trading: TradingConfig
    strategy: StrategyConfig

    def evaluate(self, snapshot: MarketSnapshot) -> SignalIntent | None:
        default_mode = self.strategy.primary_timeframe_mode if self.strategy.primary_timeframe_mode == "1h_primary" else "hybrid"
        return self.evaluate_explain(snapshot, timeframe_mode=default_mode).signal

    def evaluate_explain(self, snapshot: MarketSnapshot, timeframe_mode: TimeframeMode = "hybrid") -> SignalEvaluation:
        mode = self._resolve_mode(timeframe_mode)
        failed: list[str] = []
        if snapshot.is_stale:
            failed.append("market_stale")
        if len(snapshot.candles_4h) < 60 or len(snapshot.candles_1h) < 80 or len(snapshot.candles_15m) < 60:
            failed.append("insufficient_history")
        if failed:
            return SignalEvaluation(signal=None, failed_reasons=failed)

        side = self._trend_side_1h(snapshot)
        if side is None:
            return SignalEvaluation(signal=None, failed_reasons=["trend_not_confirmed"])
        if side == Side.LONG and not self.trading.allow_long:
            return SignalEvaluation(signal=None, failed_reasons=["long_disabled"])
        if side == Side.SHORT and not self.trading.allow_short:
            return SignalEvaluation(signal=None, failed_reasons=["short_disabled"])

        if not self._setup_valid_1h(snapshot, side):
            return SignalEvaluation(signal=None, failed_reasons=["setup_not_confirmed"])

        trigger = self._trigger_valid_15m(snapshot, side, mode)
        if not trigger.ok:
            return SignalEvaluation(signal=None, failed_reasons=["trigger_not_confirmed"])

        confidence, confidence_codes, confidence_failed = self._score_signal(snapshot, side, trigger, mode)
        if confidence_failed:
            return SignalEvaluation(signal=None, failed_reasons=confidence_failed)

        entry = snapshot.mark_price
        atr_15 = atr(snapshot.candles_15m, period=14)
        if atr_15 <= 0:
            return SignalEvaluation(signal=None, failed_reasons=["atr_invalid"])

        structure_low = min(c.low for c in snapshot.candles_1h[-12:])
        structure_high = max(c.high for c in snapshot.candles_1h[-12:])
        if side == Side.LONG:
            structure_stop = structure_low - 0.25 * atr_15
            vol_stop = entry - 1.1 * atr_15
            initial_stop = min(structure_stop, vol_stop)
        else:
            structure_stop = structure_high + 0.25 * atr_15
            vol_stop = entry + 1.1 * atr_15
            initial_stop = max(structure_stop, vol_stop)
        if abs(entry - initial_stop) <= 0:
            return SignalEvaluation(signal=None, failed_reasons=["risk_distance_invalid"])

        reasons = [
            f"trend:{'1h_long' if side == Side.LONG else '1h_short'}",
            "setup:pullback",
            f"trigger:{trigger.trigger_code}",
            *trigger.confirmation_codes,
            *confidence_codes,
            f"timeframe:{mode}",
        ]
        signal = SignalIntent(
            symbol=snapshot.symbol,
            side=side,
            entry_price=entry,
            initial_stop=initial_stop,
            confidence=confidence,
            reason_codes=reasons,
        )
        return SignalEvaluation(signal=signal, failed_reasons=[])

    def _resolve_mode(self, timeframe_mode: TimeframeMode) -> TimeframeMode:
        if timeframe_mode in {"1h", "hybrid"}:
            return "1h_primary"
        return timeframe_mode

    def _trend_side_1h(self, snapshot: MarketSnapshot) -> Side | None:
        closes = [c.close for c in snapshot.candles_1h]
        ema_fast = ema(closes, self.strategy.ema_fast_period)
        ema_mid = ema(closes, self.strategy.ema_mid_period)
        ema_slow = ema(closes, self.strategy.ema_slow_period)
        if len(ema_slow) < 3:
            return None

        last_close = closes[-1]
        atr_1h = atr(snapshot.candles_1h[-40:], period=14)
        spread = abs(ema_fast[-1] - ema_mid[-1])
        is_compressed = atr_1h > 0 and spread < (atr_1h * 0.12)

        long_ok = (
            ema_fast[-1] > ema_mid[-1] > ema_slow[-1]
            and ema_fast[-1] > ema_fast[-2]
            and ema_mid[-1] >= ema_mid[-2]
            and last_close >= ema_mid[-1]
            and not is_compressed
        )
        short_ok = (
            ema_fast[-1] < ema_mid[-1] < ema_slow[-1]
            and ema_fast[-1] < ema_fast[-2]
            and ema_mid[-1] <= ema_mid[-2]
            and last_close <= ema_mid[-1]
            and not is_compressed
        )

        if long_ok:
            return Side.LONG
        if short_ok:
            return Side.SHORT
        return None

    def _trend_bias_4h(self, snapshot: MarketSnapshot, side: Side) -> bool:
        closes = [c.close for c in snapshot.candles_4h]
        ema_mid = ema(closes, self.strategy.ema_mid_period)
        ema_slow = ema(closes, self.strategy.ema_slow_period)
        if len(ema_slow) < 3:
            return False
        last_close = closes[-1]
        if side == Side.LONG:
            return ema_mid[-1] > ema_slow[-1] and last_close >= ema_mid[-1]
        return ema_mid[-1] < ema_slow[-1] and last_close <= ema_mid[-1]

    def _setup_valid_1h(self, snapshot: MarketSnapshot, side: Side) -> bool:
        closes = [c.close for c in snapshot.candles_1h]
        ema_fast = ema(closes, self.strategy.ema_fast_period)
        ema_mid = ema(closes, self.strategy.ema_mid_period)
        last_close = closes[-1]
        recent_lows = [c.low for c in snapshot.candles_1h[-8:]]
        recent_highs = [c.high for c in snapshot.candles_1h[-8:]]

        if side == Side.LONG:
            touched_band = min(recent_lows) <= ema_fast[-1] * 1.01
            recovered = last_close >= ema_fast[-1] * 0.998 and last_close >= ema_mid[-1] * 0.995
            return touched_band and recovered

        touched_band = max(recent_highs) >= ema_fast[-1] * 0.99
        recovered = last_close <= ema_fast[-1] * 1.002 and last_close <= ema_mid[-1] * 1.005
        return touched_band and recovered

    def _trigger_valid_15m(self, snapshot: MarketSnapshot, side: Side, mode: TimeframeMode) -> TriggerEvaluation:
        closes = [c.close for c in snapshot.candles_15m]
        volumes = [c.volume for c in snapshot.candles_15m]
        ema_fast = ema(closes, self.strategy.ema_fast_period)
        bb_upper, bb_mid, bb_lower = bollinger_bands(closes, self.strategy.bb_period, self.strategy.bb_stddev)
        current = snapshot.candles_15m[-1]
        previous = snapshot.candles_15m[-2]
        lookback = self.strategy.trigger_breakout_lookback_15m if mode != "15m" else max(4, self.strategy.trigger_breakout_lookback_15m - 1)
        highs = rolling_high([c.high for c in snapshot.candles_15m], lookback + 1)
        lows = rolling_low([c.low for c in snapshot.candles_15m], lookback + 1)
        volume_sma = sma(volumes, self.strategy.trigger_volume_sma_period)

        volume_ratio = current.volume / volume_sma[-1] if volume_sma and volume_sma[-1] > 0 else 1.0
        confirmations: list[str] = []
        if volume_ratio >= self.strategy.trigger_volume_multiplier:
            confirmations.append("confirm:volume_ok")

        if side == Side.LONG:
            bb_reclaim = previous.close <= bb_mid[-2] and current.close > bb_mid[-1] and current.close >= ema_fast[-1]
            breakout = current.close > highs[-2] and current.close > bb_mid[-1]
            if bb_reclaim:
                if current.close <= bb_upper[-1] * 1.01:
                    confirmations.append("confirm:bb_ok")
                return TriggerEvaluation(True, "bb_mid_reclaim", confirmations)
            if breakout:
                if current.close <= bb_upper[-1] * 1.03:
                    confirmations.append("confirm:bb_ok")
                return TriggerEvaluation(True, "structure_breakout", confirmations)
            return TriggerEvaluation(False, "", [])

        bb_reject = previous.close >= bb_mid[-2] and current.close < bb_mid[-1] and current.close <= ema_fast[-1]
        breakdown = current.close < lows[-2] and current.close < bb_mid[-1]
        if bb_reject:
            if current.close >= bb_lower[-1] * 0.99:
                confirmations.append("confirm:bb_ok")
            return TriggerEvaluation(True, "bb_mid_reject", confirmations)
        if breakdown:
            if current.close >= bb_lower[-1] * 0.97:
                confirmations.append("confirm:bb_ok")
            return TriggerEvaluation(True, "structure_breakdown", confirmations)
        return TriggerEvaluation(False, "", [])

    def _score_signal(
        self,
        snapshot: MarketSnapshot,
        side: Side,
        trigger: TriggerEvaluation,
        mode: TimeframeMode,
    ) -> tuple[float, list[str], list[str]]:
        closes_15m = [c.close for c in snapshot.candles_15m]
        rsi_values = rsi(closes_15m, self.strategy.rsi_period)
        current_rsi = rsi_values[-1] if rsi_values else 50.0

        confidence = 0.58
        reasons: list[str] = []
        failed: list[str] = []

        if side == Side.LONG:
            if not (self.strategy.rsi_long_min <= current_rsi <= self.strategy.rsi_long_max):
                failed.append("rsi_out_of_range")
        else:
            if not (self.strategy.rsi_short_min <= current_rsi <= self.strategy.rsi_short_max):
                failed.append("rsi_out_of_range")

        if failed:
            return (0.0, reasons, failed)

        reasons.append("confirm:rsi_ok")
        confidence += 0.06

        if "confirm:bb_ok" in trigger.confirmation_codes:
            confidence += 0.04
        if "confirm:volume_ok" in trigger.confirmation_codes:
            confidence += 0.05

        if self.strategy.use_4h_trend_bias and self._trend_bias_4h(snapshot, side):
            confidence += 0.06
            reasons.append("confirm:4h_bias_aligned")

        if snapshot.atr_1h_percentile < 0.12:
            confidence -= 0.06
            reasons.append("confirm:volatility_low")
        elif snapshot.atr_1h_percentile > 0.92:
            confidence -= 0.04
            reasons.append("confirm:volatility_high")

        if snapshot.risk_extreme:
            confidence -= 0.06
            reasons.append("confirm:risk_extreme")

        if mode == "15m":
            confidence -= 0.03

        confidence = max(0.0, min(0.95, round(confidence, 2)))
        if confidence < self.strategy.signal_min_confidence:
            failed.append("confidence_below_threshold")
        return (confidence, reasons, failed)
