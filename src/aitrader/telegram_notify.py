from __future__ import annotations

from dataclasses import dataclass

import httpx

from .config import TelegramConfig


@dataclass(slots=True)
class TelegramNotifier:
    config: TelegramConfig
    timeout_seconds: float = 8.0

    def enabled(self) -> bool:
        return self.config.enabled and bool(self.config.bot_token) and bool(self.config.chat_id)

    def send_text(self, text: str) -> tuple[bool, str]:
        if not self.enabled():
            return (False, "telegram_disabled_or_missing_credentials")
        payload = {
            "chat_id": self.config.chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        return self._post("sendMessage", payload)

    def get_updates(self, offset: int | None = None, timeout_seconds: int = 20) -> tuple[list[dict[str, object]], str]:
        if not self.enabled():
            return ([], "telegram_disabled_or_missing_credentials")
        payload: dict[str, object] = {"timeout": timeout_seconds}
        if offset is not None:
            payload["offset"] = offset
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                resp = client.get(
                    f"https://api.telegram.org/bot{self.config.bot_token}/getUpdates",
                    params=payload,
                )
            if resp.status_code // 100 != 2:
                return ([], f"telegram_http_{resp.status_code}")
            body = resp.json()
            if not body.get("ok"):
                return ([], "telegram_api_not_ok")
            result = body.get("result", [])
            return (result if isinstance(result, list) else [], "ok")
        except Exception as exc:
            return ([], f"telegram_exception:{type(exc).__name__}")

    def _post(self, method: str, payload: dict[str, object]) -> tuple[bool, str]:
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                resp = client.post(f"https://api.telegram.org/bot{self.config.bot_token}/{method}", json=payload)
            if resp.status_code // 100 != 2:
                return (False, f"telegram_http_{resp.status_code}")
            body = resp.json()
            if not body.get("ok"):
                return (False, "telegram_api_not_ok")
            return (True, "ok")
        except Exception as exc:
            return (False, f"telegram_exception:{type(exc).__name__}")
