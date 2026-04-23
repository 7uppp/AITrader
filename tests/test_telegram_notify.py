from aitrader.config import TelegramConfig
from aitrader.telegram_notify import TelegramNotifier


def test_notifier_disabled_without_credentials():
    notifier = TelegramNotifier(TelegramConfig(enabled=True, bot_token="", chat_id="", send_rejections=False))
    ok, reason = notifier.send_text("hello")
    assert not ok
    assert reason == "telegram_disabled_or_missing_credentials"


def test_long_poll_uses_client_timeout_longer_than_poll_timeout(monkeypatch):
    captured: dict[str, float] = {}

    class _Response:
        status_code = 200

        @staticmethod
        def json() -> dict[str, object]:
            return {"ok": True, "result": []}

    class _Client:
        def __init__(self, timeout: float) -> None:
            captured["timeout"] = float(timeout)

        def __enter__(self) -> "_Client":
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def get(self, url: str, params: dict[str, object]) -> _Response:
            _ = url, params
            return _Response()

    monkeypatch.setattr("aitrader.telegram_notify.httpx.Client", _Client)

    notifier = TelegramNotifier(
        TelegramConfig(enabled=True, bot_token="token", chat_id="1", send_rejections=False),
        timeout_seconds=8.0,
    )
    updates, reason = notifier.get_updates(offset=1, timeout_seconds=25)

    assert reason == "ok"
    assert updates == []
    assert captured["timeout"] >= 30.0
