from pathlib import Path

from aitrader.config import AppConfig


def _write_config(path: Path, hyperliquid_block: str = "") -> None:
    path.write_text(
        f"""
[system]
name = "aitrader"
mode = "RUNNING"
timezone = "UTC"

[trading]
symbols = ["BTCUSDT"]
leverage = 1.0
max_leverage_hard = 3.0
allow_long = true
allow_short = true

[strategy]
main_lot_ratio = 0.6
runner_lot_ratio = 0.4
breakout_lookback = 10
volume_multiplier = 1.2
runner_trailing_activation_r = 1.5
runner_trailing_atr_mult = 2.2
runner_trailing_atr_mult_tight = 1.8
risk_extreme_mode_tighten_trailing = true

[risk]
single_trade_risk_pct = 0.25
daily_loss_limit_pct = 1.0
weekly_loss_limit_pct = 3.0
max_drawdown_pct = 6.0
max_consecutive_losses = 4
max_symbol_notional_pct = 12.0
max_open_positions = 2
max_open_risk_pct = 0.75
min_free_margin_pct = 70.0
liquidation_buffer_pct_major = 12.0
liquidation_buffer_pct_alt = 15.0
min_liq_distance_atr_mult = 6.0
min_liq_stop_distance_ratio = 2.5
extreme_funding_abs_pct = 0.05
hot_funding_abs_pct = 0.03
max_oi_change_1h_pct = 8.0
maintenance_margin_rate = 0.005
fee_buffer_bps = 8.0
slippage_buffer_bps = 10.0
tick_buffer_bps = 1.0

[runtime]
database_path = "data/aitrader.db"
dry_run = true
loop_interval_seconds = 15
advisory_only = true
telegram_offset_path = "data/telegram_offset.txt"

{hyperliquid_block}
""".strip()
        + "\n",
        encoding="utf-8",
    )


def test_default_hyperliquid_network_is_testnet(tmp_path: Path, monkeypatch) -> None:
    cfg_path = tmp_path / "config.toml"
    _write_config(cfg_path)
    monkeypatch.delenv("AITRADER_HL_NETWORK", raising=False)
    cfg = AppConfig.load(cfg_path)
    assert cfg.hyperliquid.network == "testnet"
    assert cfg.hyperliquid.api_url == "https://api.hyperliquid-testnet.xyz"
    assert cfg.hyperliquid.ws_url == "wss://api.hyperliquid-testnet.xyz/ws"


def test_env_can_override_network_to_mainnet(tmp_path: Path, monkeypatch) -> None:
    cfg_path = tmp_path / "config.toml"
    _write_config(
        cfg_path,
        """
[hyperliquid]
network = "testnet"
api_url = "https://api.hyperliquid-testnet.xyz"
ws_url = "wss://api.hyperliquid-testnet.xyz/ws"
vault_address = ""
private_key = ""
request_timeout_seconds = 8.0
""".strip(),
    )
    monkeypatch.setenv("AITRADER_HL_NETWORK", "mainnet")
    cfg = AppConfig.load(cfg_path)
    assert cfg.hyperliquid.network == "mainnet"
    assert cfg.hyperliquid.api_url == "https://api.hyperliquid.xyz"
    assert cfg.hyperliquid.ws_url == "wss://api.hyperliquid.xyz/ws"
