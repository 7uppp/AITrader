from datetime import UTC, datetime, timedelta

from aitrader.config import StrategyConfig, TradingConfig
from aitrader.strategy import SignalEngine
from aitrader.types import Candle, MarketSnapshot


def _strategy_cfg() -> StrategyConfig:
    return StrategyConfig(
        main_lot_ratio=0.6,
        runner_lot_ratio=0.4,
        breakout_lookback=10,
        volume_multiplier=1.2,
        runner_trailing_activation_r=1.5,
        runner_trailing_atr_mult=2.2,
        runner_trailing_atr_mult_tight=1.8,
        risk_extreme_mode_tighten_trailing=True,
    )


def _engine() -> SignalEngine:
    return SignalEngine(
        trading=TradingConfig(symbols=["BTCUSDT"], leverage=1.0, max_leverage_hard=3.0, allow_long=True, allow_short=True),
        strategy=_strategy_cfg(),
    )


def _candles_from_closes(now: datetime, closes: list[float], step_minutes: int, base_volume: float, last_volume: float | None = None) -> list[Candle]:
    candles: list[Candle] = []
    for idx, close in enumerate(closes):
        ts = now - timedelta(minutes=(len(closes) - idx) * step_minutes)
        open_price = closes[idx - 1] if idx > 0 else close
        high = max(open_price, close) + 0.6
        low = min(open_price, close) - 0.6
        volume = last_volume if last_volume is not None and idx == len(closes) - 1 else base_volume + (idx % 5) * 15.0
        candles.append(Candle(ts=ts, open=open_price, high=high, low=low, close=close, volume=volume))
    return candles


def _build_snapshot(side: str) -> MarketSnapshot:
    now = datetime.now(UTC)
    if side == "long":
        closes_4h = [100.0 + i * 1.5 for i in range(80)]
        closes_1h = (
            [120.0 + i * 0.45 for i in range(90)]
            + [160.5 - i * 0.35 for i in range(15)]
            + [155.5 + i * 0.55 for i in range(15)]
        )
        closes_15m = (
            [150.0 + i * 0.12 for i in range(40)]
            + [154.8 - i * 0.18 for i in range(14)]
            + [152.0, 151.8, 152.1, 151.9, 152.3, 152.1, 152.4, 152.2, 152.6, 152.4, 152.8, 152.6, 152.9, 152.4, 153.8]
        )
    elif side == "short":
        closes_4h = [220.0 - i * 1.6 for i in range(80)]
        closes_1h = (
            [210.0 - i * 0.45 for i in range(90)]
            + [169.5 + i * 0.35 for i in range(15)]
            + [174.5 - i * 0.55 for i in range(15)]
        )
        closes_15m = (
            [176.0 - i * 0.12 for i in range(40)]
            + [171.2 + i * 0.18 for i in range(14)]
            + [174.0, 174.2, 173.9, 174.1, 173.7, 173.9, 173.6, 173.8, 173.4, 173.6, 173.2, 173.4, 173.1, 173.7, 172.6]
        )
    else:
        closes_4h = [100.0 for _ in range(80)]
        closes_1h = [100.0 for _ in range(120)]
        closes_15m = [100.0 for _ in range(80)]

    candles_4h = _candles_from_closes(now, closes_4h, step_minutes=240, base_volume=900.0)
    candles_1h = _candles_from_closes(now, closes_1h, step_minutes=60, base_volume=700.0)
    candles_15m = _candles_from_closes(now, closes_15m, step_minutes=15, base_volume=450.0, last_volume=900.0)
    return MarketSnapshot(
        symbol="BTCUSDT",
        ts=now,
        candles_4h=candles_4h,
        candles_1h=candles_1h,
        candles_15m=candles_15m,
        mark_price=candles_15m[-1].close,
        index_price=candles_15m[-1].close,
        funding_rate_pct=0.01,
        oi_change_1h_pct=1.2,
        long_short_ratio=1.0,
        bid_ask_spread_bps=2.0,
        estimated_slippage_bps=3.0,
        atr_1h_percentile=0.55,
        is_stale=False,
        risk_extreme=False,
    )


def test_signal_engine_explain_trend_not_confirmed_for_flat_market():
    out = _engine().evaluate_explain(_build_snapshot("flat"))
    assert out.signal is None
    assert "trend_not_confirmed" in out.failed_reasons


def test_signal_engine_generates_long_signal_for_1h_primary_setup():
    out = _engine().evaluate_explain(_build_snapshot("long"), timeframe_mode="1h_primary")
    assert out.signal is not None
    assert out.signal.side.value == "LONG"
    assert any(code.startswith("trigger:") for code in out.signal.reason_codes)
    assert "timeframe:1h_primary" in out.signal.reason_codes


def test_signal_engine_generates_short_signal_for_1h_primary_setup():
    out = _engine().evaluate_explain(_build_snapshot("short"), timeframe_mode="1h_primary")
    assert out.signal is not None
    assert out.signal.side.value == "SHORT"
    assert any(code.startswith("trigger:") for code in out.signal.reason_codes)
    assert "timeframe:1h_primary" in out.signal.reason_codes
