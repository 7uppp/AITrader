from aitrader.config import RiskConfig, StrategyConfig
from aitrader.position_manager import PositionManager
from aitrader.types import Side, SignalIntent


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


def test_main_tp_arms_runner_breakeven():
    pm = PositionManager(strategy=_strategy_cfg(), risk=_risk_cfg())
    signal = SignalIntent(symbol="BTCUSDT", side=Side.LONG, entry_price=100.0, initial_stop=96.0, confidence=0.8)
    lots = pm.open_split_position(signal, quantity=10.0)
    assert len(lots) == 2
    events = pm.update(symbol="BTCUSDT", last_price=104.0, atr_15m=1.0, is_15m_close=True)
    assert "main:tp_hit" in events
    assert "runner:breakeven_armed" in events
