from datetime import UTC, datetime, timedelta

from aitrader.advisory import advisory_to_telegram_text, build_trade_advisory
from aitrader.config import (
    AppConfig,
    BinanceConfig,
    RiskConfig,
    RuntimeConfig,
    StrategyConfig,
    SystemConfig,
    TelegramConfig,
    TelemetryConfig,
    TradingConfig,
)
from aitrader.types import Candle, MarketSnapshot, RiskDecision, Side, SignalIntent, SystemMode


def _cfg() -> AppConfig:
    return AppConfig(
        system=SystemConfig(name="aitrader", mode=SystemMode.RUNNING, timezone="UTC"),
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
        risk=RiskConfig(
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
        ),
        telemetry=TelemetryConfig(log_level="INFO"),
        runtime=RuntimeConfig(
            database_path="data/aitrader.db",
            dry_run=True,
            loop_interval_seconds=15,
            advisory_only=True,
            telegram_offset_path="data/telegram_offset.txt",
        ),
        binance=BinanceConfig(base_url="https://fapi.binance.com", request_timeout_seconds=8.0),
        telegram=TelegramConfig(enabled=False, bot_token="", chat_id="", send_rejections=False),
    )


def _snapshot() -> MarketSnapshot:
    now = datetime.now(UTC)
    candles = [
        Candle(ts=now - timedelta(minutes=15 * i), open=100, high=101, low=99, close=100.5, volume=1000)
        for i in range(80, 0, -1)
    ]
    return MarketSnapshot(
        symbol="BTCUSDT",
        ts=now,
        candles_4h=candles[:60],
        candles_1h=candles[:60],
        candles_15m=candles,
        mark_price=100.0,
        index_price=100.0,
        funding_rate_pct=0.01,
        oi_change_1h_pct=1.0,
        long_short_ratio=1.0,
        bid_ask_spread_bps=3.0,
        estimated_slippage_bps=5.0,
        atr_1h_percentile=0.5,
        risk_extreme=False,
    )


def test_build_advisory_for_long():
    cfg = _cfg()
    sig = SignalIntent(
        symbol="BTCUSDT",
        side=Side.LONG,
        entry_price=100.0,
        initial_stop=96.0,
        confidence=0.8,
        reason_codes=["trigger:bb_mid_reclaim", "timeframe:1h_primary"],
    )
    decision = RiskDecision(approved=True, reason_codes=["risk:approved"], quantity=0.25)
    ad = build_trade_advisory(cfg, _snapshot(), sig, decision, atr_15m=1.2)
    assert ad.main_take_profit == 104.0
    assert ad.runner_activation_price == 106.0
    assert ad.stop_loss == 96.0
    assert ad.main_quantity == 0.15
    assert ad.runner_quantity == 0.1
    assert ad.advice_id.startswith("A-BTC")
    text = advisory_to_telegram_text(ad, _snapshot())
    assert "AdviceID" in text
    assert "1H主导 / 15m触发" in text
    assert "触发类型: 布林中轨收复" in text
    assert "触发开仓价" in text
    assert "主仓建议(60%)" in text
    assert "1R定义" in text
