from __future__ import annotations

from dataclasses import dataclass, field

from .types import ControlAction, SystemMode


COMMAND_TO_ACTION: dict[str, ControlAction] = {
    "/status": "STATUS",
    "/positions": "POSITIONS",
    "/pnl": "PNL",
    "/pause": "PAUSE",
    "/resume": "RESUME",
    "/closeall": "CLOSE_ALL",
    "/killswitch": "KILL_SWITCH",
    "/riskoff": "RISK_OFF",
}


@dataclass(slots=True)
class TelegramControl:
    mode: SystemMode
    audit_log: list[str] = field(default_factory=list)

    def handle_command(self, raw: str) -> ControlAction:
        cmd = raw.strip().split(" ")[0].lower()
        action = COMMAND_TO_ACTION.get(cmd, "NOOP")
        if action == "NOOP":
            return action

        if action == "KILL_SWITCH":
            self.mode = SystemMode.KILLED
            self.audit_log.append("mode->KILLED")
            return action
        if action == "CLOSE_ALL":
            if self.mode != SystemMode.KILLED:
                self.mode = SystemMode.PAUSED
                self.audit_log.append("mode->PAUSED")
            return action
        if action == "RISK_OFF" and self.mode not in {SystemMode.KILLED}:
            self.mode = SystemMode.RISK_OFF
            self.audit_log.append("mode->RISK_OFF")
            return action
        if action == "PAUSE" and self.mode not in {SystemMode.KILLED}:
            self.mode = SystemMode.PAUSED
            self.audit_log.append("mode->PAUSED")
            return action
        if action == "RESUME" and self.mode not in {SystemMode.KILLED}:
            self.mode = SystemMode.RUNNING
            self.audit_log.append("mode->RUNNING")
            return action
        return action
