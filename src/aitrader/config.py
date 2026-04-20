from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import tomllib

from .types import SystemMode


@dataclass(slots=True)
class SystemConfig:
    name: str
    mode: SystemMode
    timezone: str


@dataclass(slots=True)
class TradingConfig:
    symbols: list[str]
    leverage: float
    max_leverage_hard: float
    allow_long: bool
    allow_short: bool


@dataclass(slots=True)
class StrategyConfig:
    main_lot_ratio: float
    runner_lot_ratio: float
    breakout_lookback: int
    volume_multiplier: float
    runner_trailing_activation_r: float
    runner_trailing_atr_mult: float
    runner_trailing_atr_mult_tight: float
    risk_extreme_mode_tighten_trailing: bool
    primary_timeframe_mode: str = "1h_primary"
    use_4h_trend_bias: bool = True
    ema_fast_period: int = 20
    ema_mid_period: int = 50
    ema_slow_period: int = 200
    rsi_period: int = 14
    rsi_long_min: float = 45.0
    rsi_long_max: float = 68.0
    rsi_short_min: float = 32.0
    rsi_short_max: float = 55.0
    bb_period: int = 20
    bb_stddev: float = 2.0
    trigger_breakout_lookback_15m: int = 6
    trigger_volume_sma_period: int = 20
    trigger_volume_multiplier: float = 1.0
    signal_min_confidence: float = 0.58


@dataclass(slots=True)
class RiskConfig:
    single_trade_risk_pct: float
    daily_loss_limit_pct: float
    weekly_loss_limit_pct: float
    max_drawdown_pct: float
    max_consecutive_losses: int
    max_symbol_notional_pct: float
    max_open_positions: int
    max_open_risk_pct: float
    min_free_margin_pct: float
    liquidation_buffer_pct_major: float
    liquidation_buffer_pct_alt: float
    min_liq_distance_atr_mult: float
    min_liq_stop_distance_ratio: float
    extreme_funding_abs_pct: float
    hot_funding_abs_pct: float
    max_oi_change_1h_pct: float
    maintenance_margin_rate: float
    fee_buffer_bps: float
    slippage_buffer_bps: float
    tick_buffer_bps: float


@dataclass(slots=True)
class TelemetryConfig:
    log_level: str


@dataclass(slots=True)
class RuntimeConfig:
    database_path: str
    dry_run: bool
    loop_interval_seconds: int
    advisory_only: bool
    telegram_offset_path: str
    advisory_cooldown_minutes: int = 10


@dataclass(slots=True)
class BinanceConfig:
    base_url: str
    request_timeout_seconds: float


@dataclass(slots=True)
class TelegramConfig:
    enabled: bool
    bot_token: str
    chat_id: str
    send_rejections: bool


@dataclass(slots=True)
class AppConfig:
    system: SystemConfig
    trading: TradingConfig
    strategy: StrategyConfig
    risk: RiskConfig
    telemetry: TelemetryConfig
    runtime: RuntimeConfig
    binance: BinanceConfig
    telegram: TelegramConfig

    @classmethod
    def load(cls, path: str | Path) -> "AppConfig":
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
        system = SystemConfig(
            name=data["system"]["name"],
            mode=SystemMode(data["system"]["mode"]),
            timezone=data["system"]["timezone"],
        )
        trading = TradingConfig(**data["trading"])
        strategy = StrategyConfig(**data["strategy"])
        risk = RiskConfig(**data["risk"])
        telemetry = TelemetryConfig(**data.get("telemetry", {"log_level": "INFO"}))
        runtime_data = data.get(
            "runtime",
            {
                "database_path": "data/aitrader.db",
                "dry_run": True,
                "loop_interval_seconds": 15,
                "advisory_only": True,
                "telegram_offset_path": "data/telegram_offset.txt",
                "advisory_cooldown_minutes": 10,
            },
        )
        runtime = RuntimeConfig(**runtime_data)
        binance_data = data.get(
            "binance",
            {"base_url": "https://fapi.binance.com", "request_timeout_seconds": 8.0},
        )
        binance = BinanceConfig(**binance_data)
        telegram_data = data.get(
            "telegram",
            {"enabled": False, "bot_token": "", "chat_id": "", "send_rejections": False},
        )
        # Security-first override: prefer environment variables for secrets.
        env_bot_token = os.getenv("AITRADER_TELEGRAM_BOT_TOKEN", "").strip()
        env_chat_id = os.getenv("AITRADER_TELEGRAM_CHAT_ID", "").strip()
        if env_bot_token:
            telegram_data["bot_token"] = env_bot_token
        if env_chat_id:
            telegram_data["chat_id"] = env_chat_id
        telegram = TelegramConfig(**telegram_data)
        return cls(
            system=system,
            trading=trading,
            strategy=strategy,
            risk=risk,
            telemetry=telemetry,
            runtime=runtime,
            binance=binance,
            telegram=telegram,
        )
