from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json

from .time_utils import utc_now

@dataclass(slots=True)
class AuditEvent:
    ts: datetime
    event_type: str
    payload: dict[str, object]


@dataclass(slots=True)
class AuditLogger:
    events: list[AuditEvent] = field(default_factory=list)

    def record(self, event_type: str, payload: dict[str, object]) -> None:
        self.events.append(AuditEvent(ts=utc_now(), event_type=event_type, payload=payload))


def config_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
