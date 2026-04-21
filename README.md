# AiTrader

Conservative, risk-first crypto trading bot skeleton with Hyperliquid execution support and Telegram control.

## What is implemented

- Modular architecture: market data, signal, risk, advisory runtime, auto execution, Telegram control.
- Mid-frequency strategy skeleton: 1H trend/setup + 15m trigger, EMA + RSI + Bollinger + volume, split position (main + runner).
- Hard risk checks: daily/weekly loss caps, drawdown cap, max open risk, liquidation buffer guardrail.
- Auto candidate ranking and risk-budget allocation (not all-in).
- Telegram commands for scan, status, active suggestions, alive check, and result logging.
- Shared Telegram access with role control: `viewer`, `trader`, `admin`.
- Dangerous admin actions (`/closeall`, `/killswitch`) require `/confirm CODE`.

## Quick start

1. Install dependencies

```bash
pip install -e .[dev]
```

2. Run tests

```bash
pytest -q
```

3. Run one cycle

```bash
python -m aitrader cycle --config config.example.toml
```

4. Run Telegram polling once

```bash
python -m aitrader tg-once --config config.example.toml
```

5. Run 24/7 service loop

```bash
python -m aitrader serve --config config.example.toml --scan-seconds 60 --poll-timeout 25
```

## Hyperliquid live mode safety switch

Default config is safe mode (`dry_run=true`, `advisory_only=true`, `auto_trade_enabled=false`).

To enable live auto-trade, you must explicitly set all three:

- `runtime.dry_run=false`
- `runtime.advisory_only=false`
- `runtime.auto_trade_enabled=true`

Recommended secret injection (do not store private key in TOML):

- `AITRADER_HL_PRIVATE_KEY`
- `AITRADER_HL_VAULT_ADDRESS` (optional)
- `AITRADER_TELEGRAM_BOT_TOKEN`
- `AITRADER_TELEGRAM_CHAT_ID`
- `AITRADER_TELEGRAM_ALLOWED_CHAT_IDS` (comma-separated, optional)
- `AITRADER_TELEGRAM_ADMIN_USER_IDS` (comma-separated, optional)
- `AITRADER_TELEGRAM_TRADER_USER_IDS` (comma-separated, optional)
- `AITRADER_TELEGRAM_VIEWER_USER_IDS` (comma-separated, optional)

## Notes

- This is a production-oriented skeleton, not a finished HFT or market-making system.
- In live mode, execution is blocked if protection-order support is unavailable.
- Leverage suggestion is confidence-based and always capped by hard limit and 5x.
- If no user role lists are configured, allowed users default to admin.
