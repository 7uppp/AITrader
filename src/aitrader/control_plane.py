from __future__ import annotations

from dataclasses import dataclass

from .types import SystemMode


@dataclass(slots=True)
class ControlPlane:
    mode: SystemMode = SystemMode.RUNNING

    def apply(self, action: str) -> SystemMode:
        if action == "KILL_SWITCH":
            self.mode = SystemMode.KILLED
            return self.mode
        if self.mode == SystemMode.KILLED:
            return self.mode
        if action == "CLOSE_ALL":
            self.mode = SystemMode.PAUSED
            return self.mode
        if action == "RISK_OFF":
            self.mode = SystemMode.RISK_OFF
            return self.mode
        if action == "PAUSE":
            self.mode = SystemMode.PAUSED
            return self.mode
        if action == "RESUME":
            self.mode = SystemMode.RUNNING
            return self.mode
        return self.mode
