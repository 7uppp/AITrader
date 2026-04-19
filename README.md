# AiTrader

Conservative, risk-first Binance USDT-margined USDⓈ-M signal advisory bot skeleton.

## What is implemented in this baseline

- Modular architecture: market data, signal, risk, advisory runtime, telegram control.
- Conservative strategy skeleton: trend + pullback + breakout, split-position (main + runner), fixed + trailing exits.
- Hard risk checks: daily/weekly loss caps, max drawdown stop, open risk limits, liquidation buffer guardrail.
- Kill-switch oriented control model with `RUNNING / PAUSED / RISK_OFF / KILLED`.
- Persistence bootstrap schema for snapshots, signals, decisions, and operator events.
- Unit tests for critical safety behavior.

## Quick start

1. Install dependencies

```bash
pip install -e .[dev]
```

2. Run tests

```bash
pytest -q
```

3. Run the demo backtest workflow

```bash
python -m aitrader demo
```

4. Run one live-data advisory cycle (public Binance market data + Telegram advisory push)

```bash
python -m aitrader cycle --config config.example.toml
```

5. Analyze now from CLI without placing orders

```bash
python -m aitrader scan --symbols BTCUSDT,ETHUSDT,BNBUSDT,DOTUSDT,SOLUSDT --tf auto
```

6. Poll Telegram once and handle commands (`/scan`, `/scan BTCUSDT`, `/status`)

```bash
python -m aitrader tg-once --config config.example.toml
```

7. Keep Telegram listener running (no need to trigger `tg-once` manually each time)

```bash
python -m aitrader tg-loop --config config.example.toml --poll-timeout 25
```

8. Run 24/7 push mode (auto-scan whitelist + Telegram command listener in one process)

```bash
python -m aitrader serve --config config.example.toml --scan-seconds 60 --poll-timeout 25

# Security-first (recommended): keep token/chat_id out of TOML
# PowerShell:
$env:AITRADER_TELEGRAM_BOT_TOKEN="your_bot_token"
$env:AITRADER_TELEGRAM_CHAT_ID="your_chat_id"
python -m aitrader serve --config config.example.toml --scan-seconds 60 --poll-timeout 25
```

## Notes

- This repository is a production-oriented skeleton, not a complete live-ready trading system.
- Current runtime is advisory-only: it does not place exchange orders.
- Before enabling notifications, fill `[telegram]` bot token/chat id in your own config file, or inject via env vars.
- Command-driven mode: the bot can stay idle and only analyze when it receives Telegram `/scan` commands.
- In auto-push mode, only suitable advisories are sent by default; `/scan` can still show unsuitable results on demand.
- Short Telegram commands supported: `btc15m`, `eth1h`, `bnb15m`, `dot1h`, `solauto`.
- Bot menu includes `/scan`, `/alive`, `/status`, `/help`, `/result`, `/win`, `/loss`.
- Budget input supported for split sizing display: `/scan BTCUSDT 500` or `btc15m 500`.
- Manual close outcome logging supported: `/result <AdviceID> win 1.2` (recommended), `/win SOLUSDT 0.8`, `/loss ETHUSDT -0.6`.
- Advisory message includes confidence-based leverage suggestion (never above hard limit and 5x).
