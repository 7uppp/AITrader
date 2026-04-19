from __future__ import annotations

from datetime import datetime, timedelta

from .advisory import advisory_to_telegram_text, build_trade_advisory
from .backtest import Backtester
from .config import AppConfig
from .position_manager import PositionManager
from .risk import RiskEngine
from .strategy import SignalEngine
from .telegram_control import TelegramControl
from .time_utils import utc_now
from .types import AccountState, Candle, MarketSnapshot


def _build_demo_snapshot(ts: datetime, price: float, symbol: str = "BTCUSDT") -> MarketSnapshot:
    candles_4h = []
    candles_1h = []
    candles_15m = []
    for i in range(220):
        base = price - 200 + i * 1.2
        c_ts = ts - timedelta(hours=(220 - i) * 4)
        candles_4h.append(Candle(ts=c_ts, open=base - 1, high=base + 2, low=base - 2, close=base + 1, volume=1000 + i))
    for i in range(120):
        base = price - 50 + i * 0.5
        c_ts = ts - timedelta(hours=(120 - i))
        candles_1h.append(Candle(ts=c_ts, open=base - 1, high=base + 1.5, low=base - 1.5, close=base + 0.5, volume=500 + i))
    for i in range(80):
        base = price - 10 + i * 0.2
        c_ts = ts - timedelta(minutes=(80 - i) * 15)
        vol = 300 + i
        if i == 79:
            vol = 900  # force breakout-volume condition
        candles_15m.append(Candle(ts=c_ts, open=base - 0.3, high=base + 0.8, low=base - 0.8, close=base + 0.6, volume=vol))
    return MarketSnapshot(
        symbol=symbol,
        ts=ts,
        candles_4h=candles_4h,
        candles_1h=candles_1h,
        candles_15m=candles_15m,
        mark_price=price,
        index_price=price,
        funding_rate_pct=0.01,
        oi_change_1h_pct=1.5,
        long_short_ratio=1.05,
        bid_ask_spread_bps=3.0,
        estimated_slippage_bps=5.0,
        atr_1h_percentile=0.55,
        is_stale=False,
        risk_extreme=False,
    )


def run_demo(config_path: str = "config.example.toml") -> None:
    cfg = AppConfig.load(config_path)
    signal_engine = SignalEngine(cfg.trading, cfg.strategy)
    risk_engine = RiskEngine(cfg.trading, cfg.risk)
    position_manager = PositionManager(cfg.strategy, cfg.risk)
    backtester = Backtester(signal_engine=signal_engine, risk_engine=risk_engine, position_manager=position_manager)
    telegram = TelegramControl(mode=cfg.system.mode)
    account = AccountState(
        equity=10_000.0,
        free_margin=8_000.0,
        daily_pnl_pct=0.0,
        weekly_pnl_pct=0.0,
        drawdown_pct=0.0,
        consecutive_losses=0,
        open_positions=0,
        open_risk_pct=0.0,
        symbol_notional_pct=0.0,
    )
    snapshots = [_build_demo_snapshot(utc_now(), 65_000.0, symbol="BTCUSDT")]
    signal = signal_engine.evaluate(snapshots[0])
    advisory_preview = ""
    if signal is not None:
        decision = risk_engine.assess(signal, snapshots[0], account, cfg.system.mode)
        if decision.approved:
            # Demo-only preview keeps advisory generation visible without placing orders.
            atr_15m = sum((c.high - c.low) for c in snapshots[0].candles_15m[-14:]) / 14.0
            advisory = build_trade_advisory(cfg, snapshots[0], signal, decision, atr_15m=atr_15m)
            advisory_preview = advisory_to_telegram_text(advisory, snapshots[0])
    result = backtester.run(snapshots, account)
    action = telegram.handle_command("/status")
    print(f"mode={telegram.mode.value} action={action} approved={result.approved_signals} rejected={result.rejected_signals} trades={result.trades}")
    for event in result.events[:5]:
        print(event)
    if advisory_preview:
        print("--- advisory preview ---")
        print(advisory_preview)


if __name__ == "__main__":
    run_demo()
