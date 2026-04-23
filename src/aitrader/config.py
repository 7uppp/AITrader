from __future__ import annotations

from dataclasses import dataclass, field
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
    time_stop_soft_minutes_1h: int = 90
    time_stop_hard_minutes_1h: int = 120
    time_stop_soft_minutes_15m: int = 45
    time_stop_hard_minutes_15m: int = 60
    stalled_progress_r_threshold: float = 0.5
    stalled_atr_percentile_max: float = 0.35


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
    assumed_equity_usd: float = 10_000.0
    auto_trade_enabled: bool = False
    max_candidates_per_cycle: int = 3
    execution_cooldown_seconds: int = 600


@dataclass(slots=True)
class BinanceConfig:
    base_url: str
    request_timeout_seconds: float


@dataclass(slots=True)
class ExchangeConfig:
    kind: str = "hyperliquid"


@dataclass(slots=True)
class HyperliquidConfig:
    api_url: str
    ws_url: str
    vault_address: str
    private_key: str
    request_timeout_seconds: float
    network: str = "testnet"


@dataclass(slots=True)
class TelegramConfig:
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""
    send_rejections: bool = False
    allowed_chat_ids: list[str] = field(default_factory=list)
    admin_user_ids: list[str] = field(default_factory=list)
    trader_user_ids: list[str] = field(default_factory=list)
    viewer_user_ids: list[str] = field(default_factory=list)
    confirm_ttl_seconds: int = 45


@dataclass(slots=True)
class AppConfig:
    system: SystemConfig
    trading: TradingConfig
    strategy: StrategyConfig
    risk: RiskConfig
    telemetry: TelemetryConfig
    runtime: RuntimeConfig
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    binance: BinanceConfig = field(default_factory=lambda: BinanceConfig(base_url="https://fapi.binance.com", request_timeout_seconds=8.0))
    hyperliquid: HyperliquidConfig = field(
        default_factory=lambda: HyperliquidConfig(
            api_url="https://api.hyperliquid-testnet.xyz",
            ws_url="wss://api.hyperliquid-testnet.xyz/ws",
            vault_address="",
            private_key="",
            request_timeout_seconds=8.0,
            network="testnet",
        )
    )
    telegram: TelegramConfig = field(
        default_factory=lambda: TelegramConfig(enabled=False, bot_token="", chat_id="", send_rejections=False)
    )

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
                "assumed_equity_usd": 10_000.0,
                "auto_trade_enabled": False,
                "max_candidates_per_cycle": 3,
                "execution_cooldown_seconds": 600,
            },
        )
        runtime = RuntimeConfig(**runtime_data)
        exchange_data = data.get("exchange", {"kind": "hyperliquid"})
        exchange = ExchangeConfig(**exchange_data)
        binance_data = data.get(
            "binance",
            {"base_url": "https://fapi.binance.com", "request_timeout_seconds": 8.0},
        )
        binance = BinanceConfig(**binance_data)
        hyperliquid_data = data.get(
            "hyperliquid",
            {
                "network": "testnet",
                "api_url": "https://api.hyperliquid.xyz",
                "ws_url": "wss://api.hyperliquid.xyz/ws",
                "vault_address": "",
                "private_key": "",
                "request_timeout_seconds": 8.0,
            },
        )
        env_hl_network = os.getenv("AITRADER_HL_NETWORK", "").strip().lower()
        env_hl_private_key = os.getenv("AITRADER_HL_PRIVATE_KEY", "").strip()
        env_hl_vault = os.getenv("AITRADER_HL_VAULT_ADDRESS", "").strip()
        if env_hl_network:
            hyperliquid_data["network"] = env_hl_network
        if env_hl_private_key:
            hyperliquid_data["private_key"] = env_hl_private_key
        if env_hl_vault:
            hyperliquid_data["vault_address"] = env_hl_vault
        network = str(hyperliquid_data.get("network", "testnet")).strip().lower()
        if network not in {"testnet", "mainnet"}:
            network = "testnet"
        hyperliquid_data["network"] = network
        hyperliquid_data["api_url"], hyperliquid_data["ws_url"] = resolve_hyperliquid_endpoints(network)
        hyperliquid = HyperliquidConfig(**hyperliquid_data)
        telegram_data = data.get(
            "telegram",
            {
                "enabled": False,
                "bot_token": "",
                "chat_id": "",
                "send_rejections": False,
                "allowed_chat_ids": [],
                "admin_user_ids": [],
                "trader_user_ids": [],
                "viewer_user_ids": [],
                "confirm_ttl_seconds": 45,
            },
        )
        # Security-first override: prefer environment variables for secrets.
        env_bot_token = os.getenv("AITRADER_TELEGRAM_BOT_TOKEN", "").strip()
        env_chat_id = os.getenv("AITRADER_TELEGRAM_CHAT_ID", "").strip()
        env_allowed_chat_ids = os.getenv("AITRADER_TELEGRAM_ALLOWED_CHAT_IDS", "").strip()
        env_admin_user_ids = os.getenv("AITRADER_TELEGRAM_ADMIN_USER_IDS", "").strip()
        env_trader_user_ids = os.getenv("AITRADER_TELEGRAM_TRADER_USER_IDS", "").strip()
        env_viewer_user_ids = os.getenv("AITRADER_TELEGRAM_VIEWER_USER_IDS", "").strip()
        if env_bot_token:
            telegram_data["bot_token"] = env_bot_token
        if env_chat_id:
            telegram_data["chat_id"] = env_chat_id
        if env_allowed_chat_ids:
            telegram_data["allowed_chat_ids"] = _split_csv(env_allowed_chat_ids)
        if env_admin_user_ids:
            telegram_data["admin_user_ids"] = _split_csv(env_admin_user_ids)
        if env_trader_user_ids:
            telegram_data["trader_user_ids"] = _split_csv(env_trader_user_ids)
        if env_viewer_user_ids:
            telegram_data["viewer_user_ids"] = _split_csv(env_viewer_user_ids)

        telegram_data["chat_id"] = str(telegram_data.get("chat_id", "")).strip()
        telegram_data["allowed_chat_ids"] = [str(v).strip() for v in telegram_data.get("allowed_chat_ids", []) if str(v).strip()]
        telegram_data["admin_user_ids"] = [str(v).strip() for v in telegram_data.get("admin_user_ids", []) if str(v).strip()]
        telegram_data["trader_user_ids"] = [str(v).strip() for v in telegram_data.get("trader_user_ids", []) if str(v).strip()]
        telegram_data["viewer_user_ids"] = [str(v).strip() for v in telegram_data.get("viewer_user_ids", []) if str(v).strip()]
        telegram = TelegramConfig(**telegram_data)
        return cls(
            system=system,
            trading=trading,
            strategy=strategy,
            risk=risk,
            telemetry=telemetry,
            runtime=runtime,
            exchange=exchange,
            binance=binance,
            hyperliquid=hyperliquid,
            telegram=telegram,
        )


def _split_csv(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def resolve_hyperliquid_endpoints(network: str) -> tuple[str, str]:
    normalized = network.strip().lower()
    if normalized == "mainnet":
        return ("https://api.hyperliquid.xyz", "wss://api.hyperliquid.xyz/ws")
    return ("https://api.hyperliquid-testnet.xyz", "wss://api.hyperliquid-testnet.xyz/ws")
