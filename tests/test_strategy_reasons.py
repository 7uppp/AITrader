from datetime import UTC, datetime, timedelta

from aitrader.config import StrategyConfig, TradingConfig
from aitrader.strategy import SignalEngine
from aitrader.types import Candle, MarketSnapshot


def _flat_snapshot(is_stale: bool = False) -> MarketSnapshot:
    now = datetime.now(UTC)
    candles_4h = []
    candles_1h = []
    candles_15m = []
    for i in range(220):
        ts = now - timedelta(hours=(220 - i) * 4)
        candles_4h.append(Candle(ts=ts, open=100, high=101, low=99, close=100, volume=1000))
    for i in range(120):
        ts = now - timedelta(hours=(120 - i))
        candles_1h.append(Candle(ts=ts, open=100, high=101, low=99, close=100, volume=800))
    for i in range(80):
        ts = now - timedelta(minutes=(80 - i) * 15)
        candles_15m.append(Candle(ts=ts, open=100, high=101, low=99, close=100, volume=500))
    return MarketSnapshot(
        symbol="BTCUSDT",
        ts=now,
        candles_4h=candles_4h,
        candles_1h=candles_1h,
        candles_15m=candles_15m,
        mark_price=100.0,
        index_price=100.0,
        funding_rate_pct=0.0,
        oi_change_1h_pct=0.0,
        long_short_ratio=1.0,
        bid_ask_spread_bps=2.0,
        estimated_slippage_bps=3.0,
        atr_1h_percentile=0.5,
        is_stale=is_stale,
    )


def test_signal_engine_explain_stale_reason():
    engine = SignalEngine(
        trading=TradingConfig(symbols=["BTCUSDT"], leverage=1.0, max_leverage_hard=3.0, allow_long=True, allow_short=True),
        strategy=StrategyConfig(
            main_lot_ratio=0.6,
            runner_lot_ratio=0.4,
            breakout_lookback=10,
            volume_multiplier=1.2,
            runner_trailing_activation_r=1.5,
            runner_trailing_atr_mult=2.2,
            runner_trailing_atr_mult_tight=1.8,
            risk_extreme_mode_tighten_trailing=True,
        ),
    )
    out = engine.evaluate_explain(_flat_snapshot(is_stale=True))
    assert out.signal is None
    assert "market_stale" in out.failed_reasons


def test_signal_engine_explain_trend_not_confirmed():
    engine = SignalEngine(
        trading=TradingConfig(symbols=["BTCUSDT"], leverage=1.0, max_leverage_hard=3.0, allow_long=True, allow_short=True),
        strategy=StrategyConfig(
            main_lot_ratio=0.6,
            runner_lot_ratio=0.4,
            breakout_lookback=10,
            volume_multiplier=1.2,
            runner_trailing_activation_r=1.5,
            runner_trailing_atr_mult=2.2,
            runner_trailing_atr_mult_tight=1.8,
            risk_extreme_mode_tighten_trailing=True,
        ),
    )
    out = engine.evaluate_explain(_flat_snapshot(is_stale=False))
    assert out.signal is None
    assert "trend_not_confirmed" in out.failed_reasons
