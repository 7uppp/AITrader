from pathlib import Path
from types import SimpleNamespace
import json

from aitrader.config import TelegramConfig
from aitrader.telegram_command_bot import TelegramCommandBot
from aitrader.types import SystemMode


def _bot(tmp_path: Path) -> TelegramCommandBot:
    class _NotifierStub:
        config = TelegramConfig(enabled=False, bot_token="", chat_id="", send_rejections=False)

        def __init__(self) -> None:
            self.sent_texts: list[str] = []
            self.commands: list[dict[str, str]] = []

        def send_text(self, text: str) -> tuple[bool, str]:
            self.sent_texts.append(text)
            return (True, "ok")

        def send_text_to_chat(self, chat_id: str, text: str) -> tuple[bool, str]:
            _ = chat_id
            self.sent_texts.append(text)
            return (True, "ok")

        def set_my_commands(self, commands):
            self.commands = list(commands)
            return (True, "ok")

        def get_updates(self, offset=None, timeout_seconds=20):
            return ([], "ok")

    storage_stub = SimpleNamespace(
        insert_system_event=lambda *args, **kwargs: None,
        insert_trade_feedback=lambda *args, **kwargs: None,
        insert_operator_command=lambda *args, **kwargs: None,
        get_advice_record=lambda advice_id: None,
        close_advice_record=lambda *args, **kwargs: None,
        has_feedback_for_advice=lambda advice_id: False,
        trade_feedback_stats=lambda symbol=None: {"total": 0, "wins": 0, "losses": 0, "win_rate_pct": 0.0},
        list_active_advices=lambda now=None: [],
        get_latest_active_advice=lambda now=None: None,
        get_advice_ids_by_suffix=lambda suffix, status=None, limit=20: [],
        get_latest_trade_feedback=lambda source=None: None,
    )
    runtime = SimpleNamespace(
        config=SimpleNamespace(
            exchange=SimpleNamespace(kind="hyperliquid"),
            trading=SimpleNamespace(symbols=["BTCUSDT", "ETHUSDT"]),
            runtime=SimpleNamespace(auto_trade_enabled=True, advisory_only=False, dry_run=True),
            hyperliquid=SimpleNamespace(
                network="testnet",
                api_url="https://api.hyperliquid-testnet.xyz",
                ws_url="wss://api.hyperliquid-testnet.xyz/ws",
            ),
        ),
        notifier=_NotifierStub(),
        storage=storage_stub,
        analyze_symbols=lambda symbols, push_to_telegram=False, timeframe_mode="auto", manual_total_usdt=None: [],
        mode=SystemMode.RUNNING,
        _refresh_account_for_symbol=lambda symbol="": None,
        switch_hyperliquid_network=lambda network: (True, network),
        execution_engine=None,
    )
    return TelegramCommandBot(runtime=runtime, notifier=runtime.notifier, offset_path=tmp_path / "offset.txt")


def test_parse_symbols_defaults_to_watchlist(tmp_path: Path):
    bot = _bot(tmp_path)
    symbols, timeframe, amount, errors = bot._parse_symbols_and_timeframe("/scan")
    assert symbols == ["BTCUSDT", "ETHUSDT"]
    assert timeframe == "auto"
    assert amount is None
    assert errors == []


def test_parse_symbols_from_command(tmp_path: Path):
    bot = _bot(tmp_path)
    symbols, timeframe, amount, errors = bot._parse_symbols_and_timeframe("/scan btcusdt,ethusdt 15m")
    assert symbols == ["BTCUSDT", "ETHUSDT"]
    assert timeframe == "15m"
    assert amount is None
    assert errors == []


def test_parse_compact_short_command(tmp_path: Path):
    bot = _bot(tmp_path)
    symbols, timeframe, amount, errors = bot._parse_symbols_and_timeframe("btc15m")
    assert symbols == ["BTCUSDT"]
    assert timeframe == "15m"
    assert amount is None
    assert errors == []


def test_parse_with_amount_for_single_symbol(tmp_path: Path):
    bot = _bot(tmp_path)
    symbols, timeframe, amount, errors = bot._parse_symbols_and_timeframe("btc15m 500")
    assert symbols == ["BTCUSDT"]
    assert timeframe == "15m"
    assert amount == 500.0
    assert errors == []


def test_parse_with_amount_requires_single_symbol(tmp_path: Path):
    bot = _bot(tmp_path)
    _, _, amount, errors = bot._parse_symbols_and_timeframe("/scan BTCUSDT ETHUSDT 500")
    assert amount == 500.0
    assert "amount_requires_single_symbol" in errors


def test_parse_new_compact_symbols(tmp_path: Path):
    bot = _bot(tmp_path)
    symbols, timeframe, amount, errors = bot._parse_symbols_and_timeframe("bnb1h dot15m solauto")
    assert symbols == ["BNBUSDT", "DOTUSDT", "SOLUSDT"]
    assert timeframe == "auto"
    assert amount is None
    assert errors == []


def test_normalize_new_symbols():
    assert TelegramCommandBot._normalize_symbol("BNB") == "BNBUSDT"
    assert TelegramCommandBot._normalize_symbol("DOTUSDT") == "DOTUSDT"
    assert TelegramCommandBot._normalize_symbol("sol") == "SOLUSDT"


def test_parse_result_command_win():
    bot = TelegramCommandBot.__new__(TelegramCommandBot)  # parse helper only
    parsed, error = TelegramCommandBot._parse_result_command(bot, "/result BTCUSDT win 1.2")
    assert error is None
    assert parsed == (None, "BTCUSDT", "WIN", 1.2, "")


def test_parse_result_command_loss_shortcut():
    bot = TelegramCommandBot.__new__(TelegramCommandBot)  # parse helper only
    parsed, error = TelegramCommandBot._parse_result_command(bot, "/loss solusdt -0.8 stop_hit")
    assert error is None
    assert parsed == (None, "SOLUSDT", "LOSS", -0.8, "stop_hit")


def test_parse_result_command_with_advice_id():
    bot = TelegramCommandBot.__new__(TelegramCommandBot)  # parse helper only
    parsed, error = TelegramCommandBot._parse_result_command(bot, "/result A-BTC-1H-20260420153012-ABC123 win 1.2")
    assert error is None
    assert parsed == ("A-BTC-1H-20260420153012-ABC123", None, "WIN", 1.2, "")


def test_resolve_advice_target_with_short_id(tmp_path: Path):
    bot = _bot(tmp_path)
    bot.runtime.storage.get_advice_ids_by_suffix = lambda suffix, status=None, limit=20: ["A-BTC-1H-20260420153012-ABC123"]
    resolved, err = bot._resolve_advice_target("abc123", now=None)
    assert err is None
    assert resolved == "A-BTC-1H-20260420153012-ABC123"


def test_resolve_advice_target_with_last(tmp_path: Path):
    bot = _bot(tmp_path)
    bot.runtime.storage.get_latest_active_advice = lambda now=None: SimpleNamespace(advice_id="A-ETH-1H-20260420153012-XYZ789")
    resolved, err = bot._resolve_advice_target("last", now=None)
    assert err is None
    assert resolved == "A-ETH-1H-20260420153012-XYZ789"


def test_menu_commands_and_alive_command(tmp_path: Path):
    bot = _bot(tmp_path)
    ok, reason = bot.ensure_menu_commands()
    assert ok
    assert reason == "ok"
    assert [item["command"] for item in bot.notifier.commands] == [
        "scan",
        "positions",
        "active",
        "alive",
        "status",
        "net",
        "help",
        "result",
        "smoketest",
        "pause",
        "resume",
        "riskoff",
        "closeall",
        "killswitch",
    ]

    bot._handle_text_command("/alive")
    assert any("bot alive" in text for text in bot.notifier.sent_texts)


def test_active_command_with_no_open_advice(tmp_path: Path):
    bot = _bot(tmp_path)
    bot._handle_text_command("/active")
    assert any("No active advices." in text for text in bot.notifier.sent_texts)


def test_active_command_with_open_advices(tmp_path: Path):
    bot = _bot(tmp_path)
    bot.runtime.storage.list_active_advices = lambda now=None: [
        SimpleNamespace(
            advice_id="A-BTC-1H-20260420153012-AAA111",
            symbol="BTCUSDT",
            side="LONG",
            entry_trigger=62000.0,
            remaining_minutes=35,
        )
    ]
    bot._handle_text_command("/active")
    payload = "\n".join(bot.notifier.sent_texts)
    assert "Active advices (1)" in payload
    assert "BTCUSDT LONG" in payload
    assert "AAA111" in payload


def test_positions_command_with_no_open_positions(tmp_path: Path):
    bot = _bot(tmp_path)
    bot.runtime.position_manager = SimpleNamespace(lots=[])
    bot._handle_text_command("/positions")
    assert any("No open positions." in text for text in bot.notifier.sent_texts)


def test_viewer_cannot_use_admin_command(tmp_path: Path):
    bot = _bot(tmp_path)
    bot._handle_text_command("/pause", chat_id="100", user_id="200", role="viewer")
    assert any("Permission denied" in text for text in bot.notifier.sent_texts)


def test_closeall_requires_confirm_then_executes(tmp_path: Path):
    bot = _bot(tmp_path)
    bot.runtime.position_manager = SimpleNamespace(lots=[], close_all=lambda: [])
    bot._handle_text_command("/closeall", chat_id="100", user_id="200", role="admin")
    key = "100:200"
    assert key in bot.pending_confirms
    code = bot.pending_confirms[key].code
    bot._handle_text_command(f"/confirm {code}", chat_id="100", user_id="200", role="admin")
    assert bot.runtime.mode == SystemMode.PAUSED


def test_net_status_is_visible_and_switch_requires_admin(tmp_path: Path):
    bot = _bot(tmp_path)
    bot._handle_text_command("/net status", chat_id="100", user_id="200", role="viewer")
    assert any("Hyperliquid network: testnet" in text for text in bot.notifier.sent_texts)

    bot.notifier.sent_texts.clear()
    bot._handle_text_command("/net mainnet", chat_id="100", user_id="200", role="viewer")
    assert any("Permission denied" in text for text in bot.notifier.sent_texts)


def test_net_mainnet_requires_confirm(tmp_path: Path):
    bot = _bot(tmp_path)
    bot._handle_text_command("/net mainnet", chat_id="100", user_id="200", role="admin")
    key = "100:200"
    assert key in bot.pending_confirms


def test_status_contains_latest_auto_settlement(tmp_path: Path):
    bot = _bot(tmp_path)
    bot.runtime.storage.get_latest_trade_feedback = lambda source=None: {
        "advice_id": "A-BTC-1H-20260420153012-ABC123",
        "symbol": "BTCUSDT",
        "pnl_pct": 1.25,
        "payload_json": json.dumps({"source": "auto_trade", "total_pnl_usd": 12.34}),
    }
    bot._handle_text_command("/status", chat_id="100", user_id="200", role="viewer")
    payload = "\n".join(bot.notifier.sent_texts)
    assert "last_auto_settlement: BTCUSDT ABC123 12.3400 USD (1.25%)" in payload


def test_smoketest_runs_on_testnet_for_admin(tmp_path: Path):
    bot = _bot(tmp_path)
    bot._handle_text_command("/smoketest btc 0.001", chat_id="100", user_id="200", role="admin")
    payload = "\n".join(bot.notifier.sent_texts)
    assert "[SMOKETEST OK]" in payload


def test_smoketest_blocked_on_mainnet(tmp_path: Path):
    bot = _bot(tmp_path)
    bot.runtime.config.hyperliquid.network = "mainnet"
    bot._handle_text_command("/smoketest btc 0.001", chat_id="100", user_id="200", role="admin")
    payload = "\n".join(bot.notifier.sent_texts)
    assert "only allowed on testnet" in payload
