from datetime import UTC, datetime, timedelta

from aitrader.config import RiskConfig, TradingConfig
from aitrader.risk import RiskEngine
from aitrader.types import AccountState, Candle, MarketSnapshot, Side, SignalIntent, SystemMode


def _snapshot(price: float = 65000.0) -> MarketSnapshot:
    now = datetime.now(UTC)
    candles_4h = [
        Candle(ts=now - timedelta(hours=(220 - i) * 4), open=price - 300 + i, high=price - 298 + i, low=price - 302 + i, close=price - 299 + i, volume=1000 + i)
        for i in range(220)
    ]
    candles_1h = [
        Candle(ts=now - timedelta(hours=(120 - i)), open=price - 100 + i * 0.2, high=price - 99 + i * 0.2, low=price - 101 + i * 0.2, close=price - 99.5 + i * 0.2, volume=600 + i)
        for i in range(120)
    ]
    candles_15m = [
        Candle(ts=now - timedelta(minutes=(80 - i) * 15), open=price - 20 + i * 0.1, high=price - 19 + i * 0.1, low=price - 21 + i * 0.1, close=price - 19.5 + i * 0.1, volume=300 + i)
        for i in range(80)
    ]
    return MarketSnapshot(
        symbol="BTCUSDT",
        ts=now,
        candles_4h=candles_4h,
        candles_1h=candles_1h,
        candles_15m=candles_15m,
        mark_price=price,
        index_price=price,
        funding_rate_pct=0.01,
        oi_change_1h_pct=1.0,
        long_short_ratio=1.0,
        bid_ask_spread_bps=3.0,
        estimated_slippage_bps=5.0,
        atr_1h_percentile=0.5,
    )


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


def _account() -> AccountState:
    return AccountState(
        equity=10000.0,
        free_margin=8500.0,
        daily_pnl_pct=0.0,
        weekly_pnl_pct=0.0,
        drawdown_pct=0.0,
        consecutive_losses=0,
        open_positions=0,
        open_risk_pct=0.0,
        symbol_notional_pct=0.0,
    )


def test_risk_rejects_when_paused():
    risk_engine = RiskEngine(
        trading=TradingConfig(symbols=["BTCUSDT"], leverage=1.0, max_leverage_hard=3.0, allow_long=True, allow_short=True),
        risk=_risk_cfg(),
    )
    signal = SignalIntent(symbol="BTCUSDT", side=Side.LONG, entry_price=65000.0, initial_stop=64000.0, confidence=0.8)
    decision = risk_engine.assess(signal, _snapshot(), _account(), SystemMode.PAUSED)
    assert not decision.approved
    assert "system:paused" in decision.reason_codes


def test_risk_rejects_extreme_funding():
    risk_engine = RiskEngine(
        trading=TradingConfig(symbols=["BTCUSDT"], leverage=1.0, max_leverage_hard=3.0, allow_long=True, allow_short=True),
        risk=_risk_cfg(),
    )
    snap = _snapshot()
    snap.funding_rate_pct = 0.08
    signal = SignalIntent(symbol="BTCUSDT", side=Side.LONG, entry_price=65000.0, initial_stop=64000.0, confidence=0.8)
    decision = risk_engine.assess(signal, snap, _account(), SystemMode.RUNNING)
    assert not decision.approved
    assert "risk:extreme_funding" in decision.reason_codes
