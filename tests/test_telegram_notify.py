from aitrader.config import TelegramConfig
from aitrader.telegram_notify import TelegramNotifier


def test_notifier_disabled_without_credentials():
    notifier = TelegramNotifier(TelegramConfig(enabled=True, bot_token="", chat_id="", send_rejections=False))
    ok, reason = notifier.send_text("hello")
    assert not ok
    assert reason == "telegram_disabled_or_missing_credentials"
