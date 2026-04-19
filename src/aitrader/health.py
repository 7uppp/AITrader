from __future__ import annotations

from dataclasses import dataclass

from .types import SystemMode


@dataclass(slots=True)
class HealthSnapshot:
    websocket_delay_ms: int
    user_stream_ok: bool
    api_error_rate_pct: float
    ntp_offset_ms: int
    telegram_ok: bool


@dataclass(slots=True)
class HealthMonitor:
    max_ws_delay_ms: int = 3000
    max_api_error_rate_pct: float = 5.0
    max_ntp_offset_ms: int = 1000

    def evaluate(self, hs: HealthSnapshot) -> tuple[SystemMode, list[str]]:
        reasons: list[str] = []
        if not hs.user_stream_ok:
            reasons.append("user_stream_down")
        if hs.websocket_delay_ms > self.max_ws_delay_ms:
            reasons.append("ws_delay_high")
        if hs.api_error_rate_pct > self.max_api_error_rate_pct:
            reasons.append("api_error_rate_high")
        if abs(hs.ntp_offset_ms) > self.max_ntp_offset_ms:
            reasons.append("clock_drift_high")
        if not hs.telegram_ok:
            reasons.append("telegram_down")
        if reasons:
            return (SystemMode.RISK_OFF, reasons)
        return (SystemMode.RUNNING, reasons)
