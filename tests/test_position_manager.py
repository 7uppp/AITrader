from datetime import UTC, datetime, timedelta

from aitrader.config import RiskConfig, StrategyConfig
from aitrader.position_manager import PositionManager
from aitrader.types import Candle, MarketSnapshot, Side, SignalIntent


def _risk_cfg() -> RiskConfig:
    return RiskConfig(
        single_trade_risk_pct=0.25,
        daily_loss_limit_pct=1.0,
        weekly_loss_limit_pct=3.0,
        max_drawdown_pct=6.0,
        max_consecutive_losses=4,
        max_symbol_notional_pct=12.0,
        max_open_positions=2,
        max_open_risk_pct=0.75,
        min_free_margin_pct=70.0,
        liquidation_buffer_pct_major=12.0,
        liquidation_buffer_pct_alt=15.0,
        min_liq_distance_atr_mult=6.0,
        min_liq_stop_distance_ratio=2.5,
        extreme_funding_abs_pct=0.05,
        hot_funding_abs_pct=0.03,
        max_oi_change_1h_pct=8.0,
        maintenance_margin_rate=0.005,
        fee_buffer_bps=8.0,
        slippage_buffer_bps=10.0,
        tick_buffer_bps=1.0,
    )


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


def _snapshot(price: float = 100.0, ts: datetime | None = None, atr_pct: float = 0.5) -> MarketSnapshot:
    now = ts or datetime.now(UTC)
    candles_15m = [
        Candle(ts=now - timedelta(minutes=15 * idx), open=price, high=price + 1, low=price - 1, close=price, volume=1000)
        for idx in range(80, 0, -1)
    ]
    candles_1h = [
        Candle(ts=now - timedelta(hours=idx), open=price, high=price + 1, low=price - 1, close=price, volume=1000)
        for idx in range(80, 0, -1)
    ]
    return MarketSnapshot(
        symbol="BTCUSDT",
        ts=now,
        candles_4h=candles_1h[:60],
        candles_1h=candles_1h[:60],
        candles_15m=candles_15m,
        mark_price=price,
        index_price=price,
        funding_rate_pct=0.01,
        oi_change_1h_pct=1.0,
        long_short_ratio=1.0,
        bid_ask_spread_bps=3.0,
        estimated_slippage_bps=5.0,
        atr_1h_percentile=atr_pct,
        risk_extreme=False,
    )


def test_main_tp_arms_runner_breakeven():
    pm = PositionManager(strategy=_strategy_cfg(), risk=_risk_cfg())
    signal = SignalIntent(symbol="BTCUSDT", side=Side.LONG, entry_price=100.0, initial_stop=96.0, confidence=0.8)
    lots = pm.open_split_position(signal, quantity=10.0)
    assert len(lots) == 2
    events = pm.update(snapshot=_snapshot(price=104.0), atr_15m=1.0, is_15m_close=True)
    assert "main:tp_hit" in events
    assert "runner:breakeven_armed" in events


def test_soft_time_stop_closes_runner_first():
    pm = PositionManager(strategy=_strategy_cfg(), risk=_risk_cfg())
    signal = SignalIntent(symbol="BTCUSDT", side=Side.LONG, entry_price=100.0, initial_stop=96.0, confidence=0.8)
    lots = pm.open_split_position(signal, quantity=10.0, opened_at=datetime.now(UTC) - timedelta(minutes=95), entry_timeframe="1h_primary")
    assert len(lots) == 2
    events = pm.update(snapshot=_snapshot(price=100.2, atr_pct=0.1), atr_15m=1.0, is_15m_close=True)
    assert "runner:time_stop_soft" in events
    main = next(l for l in pm.lots if l.kind.value == "MAIN")
    runner = next(l for l in pm.lots if l.kind.value == "RUNNER")
    assert main.active
    assert not runner.active
    assert runner.exit_reason == "time_stop_soft"
