from pathlib import Path
from types import SimpleNamespace

from aitrader.config import TelegramConfig
from aitrader.telegram_command_bot import TelegramCommandBot


def _bot(tmp_path: Path) -> TelegramCommandBot:
    class _NotifierStub:
        config = TelegramConfig(enabled=False, bot_token="", chat_id="", send_rejections=False)

        def __init__(self) -> None:
            self.sent_texts: list[str] = []
            self.commands: list[dict[str, str]] = []

        def send_text(self, text: str) -> tuple[bool, str]:
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
    )
    runtime = SimpleNamespace(
        config=SimpleNamespace(
            trading=SimpleNamespace(symbols=["BTCUSDT", "ETHUSDT"]),
        ),
        notifier=_NotifierStub(),
        storage=storage_stub,
        analyze_symbols=lambda symbols, push_to_telegram=False, timeframe_mode="auto", manual_total_usdt=None: [],
        mode=SimpleNamespace(value="RUNNING"),
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


def test_menu_commands_and_alive_command(tmp_path: Path):
    bot = _bot(tmp_path)
    ok, reason = bot.ensure_menu_commands()
    assert ok
    assert reason == "ok"
    assert [item["command"] for item in bot.notifier.commands] == ["scan", "alive", "status", "help", "result", "win", "loss"]

    bot._handle_text_command("/alive")
    assert any("bot alive" in text for text in bot.notifier.sent_texts)
