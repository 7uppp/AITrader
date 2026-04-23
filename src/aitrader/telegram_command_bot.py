from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
import re
import secrets
import string

from .runtime import TradingRuntime
from .telegram_notify import TelegramNotifier
from .time_utils import utc_now
from .types import SystemMode

SUPPORTED_TIMEFRAMES = {"15m", "1h", "hybrid", "1h_primary", "auto"}
COMPACT_SYMBOLS = {
    "btc": "BTCUSDT",
    "eth": "ETHUSDT",
    "bnb": "BNBUSDT",
    "dot": "DOTUSDT",
    "sol": "SOLUSDT",
}
COMPACT_PATTERN = re.compile(r"^(btc|eth|bnb|dot|sol)(15m|1h|hybrid|auto)?$")

VIEWER_COMMANDS = {
    "/scan",
    "/active",
    "/alive",
    "/ping",
    "/status",
    "/help",
    "/positions",
    "/net",
}
TRADER_COMMANDS = VIEWER_COMMANDS | {"/result", "/win", "/loss"}
ADMIN_COMMANDS = TRADER_COMMANDS | {"/pause", "/resume", "/riskoff", "/closeall", "/killswitch", "/confirm"}
DANGEROUS_COMMANDS = {"/closeall", "/killswitch"}


@dataclass(slots=True)
class PendingConfirm:
    action: str
    code: str
    expires_at: datetime


@dataclass(slots=True)
class TelegramCommandBot:
    runtime: TradingRuntime
    notifier: TelegramNotifier
    offset_path: Path
    menu_sync_attempted: bool = False
    pending_confirms: dict[str, PendingConfirm] = field(default_factory=dict)

    @classmethod
    def from_runtime(cls, runtime: TradingRuntime) -> "TelegramCommandBot":
        offset_path = Path(runtime.config.runtime.telegram_offset_path)
        offset_path.parent.mkdir(parents=True, exist_ok=True)
        return cls(runtime=runtime, notifier=runtime.notifier, offset_path=offset_path)

    def run_once(self, timeout_seconds: int = 25) -> str:
        self.ensure_menu_commands()
        offset = self._load_offset()
        updates, reason = self.notifier.get_updates(offset=offset, timeout_seconds=timeout_seconds)
        if reason != "ok":
            self.runtime.storage.insert_system_event(utc_now(), "telegram_poll_error", {"reason": reason})
            return f"poll_error:{reason}"
        if not updates:
            return "no_updates"

        max_update_id = offset or 0
        handled = 0
        for update in updates:
            update_id = int(update.get("update_id", 0))
            max_update_id = max(max_update_id, update_id + 1)
            message = update.get("message")
            if not isinstance(message, dict):
                continue
            chat = message.get("chat", {})
            chat_id = str(chat.get("id", "")).strip()
            user = message.get("from", {})
            user_id = str(user.get("id", "")).strip()
            text = str(message.get("text", "")).strip()
            if not text:
                continue

            if not self._is_allowed_chat(chat_id):
                continue
            role = self._resolve_role(user_id)
            if role is None:
                self._reply(chat_id, "You are not authorized to use this bot.")
                self.runtime.storage.insert_system_event(
                    utc_now(),
                    "telegram_user_unauthorized",
                    {"chat_id": chat_id, "user_id": user_id, "text": text},
                )
                continue

            try:
                self._handle_text_command(text, chat_id=chat_id, user_id=user_id, role=role)
                handled += 1
            except Exception as exc:
                self.runtime.storage.insert_system_event(
                    utc_now(),
                    "telegram_command_handler_error",
                    {
                        "chat_id": chat_id,
                        "user_id": user_id,
                        "role": role,
                        "text": text,
                        "error": type(exc).__name__,
                        "message": str(exc),
                    },
                )
                self._reply(chat_id, f"Command failed: {type(exc).__name__}. Check logs and retry.")

        self._save_offset(max_update_id)
        return f"handled:{handled}"

    def _handle_text_command(self, text: str, chat_id: str = "", user_id: str = "", role: str = "admin") -> None:
        normalized = text.strip()
        lower = normalized.lower()
        command_key = self._command_key(lower)
        if not self._role_allows(role, command_key):
            self._reply(chat_id, f"Permission denied for role={role}.")
            self.runtime.storage.insert_system_event(
                utc_now(),
                "telegram_permission_denied",
                {"chat_id": chat_id, "user_id": user_id, "role": role, "text": text},
            )
            return

        if lower.startswith("/confirm"):
            self._handle_confirm_command(normalized, chat_id=chat_id, user_id=user_id)
            return

        if command_key in DANGEROUS_COMMANDS:
            self._request_danger_confirm(action=command_key, chat_id=chat_id, user_id=user_id)
            return

        if lower.startswith("/pause"):
            self._set_mode(SystemMode.PAUSED, chat_id=chat_id, event="telegram_pause_command")
            return
        if lower.startswith("/resume"):
            if self.runtime.mode == SystemMode.KILLED:
                self._reply(chat_id, "Cannot /resume from KILLED. Use manual reset.")
                return
            self._set_mode(SystemMode.RUNNING, chat_id=chat_id, event="telegram_resume_command")
            return
        if lower.startswith("/riskoff"):
            if self.runtime.mode == SystemMode.KILLED:
                self._reply(chat_id, "Cannot switch mode from KILLED.")
                return
            self._set_mode(SystemMode.RISK_OFF, chat_id=chat_id, event="telegram_riskoff_command")
            return

        if lower.startswith("/net"):
            self._handle_net_command(normalized, chat_id=chat_id, user_id=user_id, role=role)
            return

        if lower.startswith("/scan") or self._looks_like_compact_scan(lower):
            symbols, timeframe, manual_total_usdt, errors = self._parse_symbols_and_timeframe(normalized)
            if errors:
                ok, reason = self._reply(
                    chat_id,
                    "Invalid command format.\n"
                    "Examples: /scan, btc15m, bnb1h, dotauto, /scan BTCUSDT 500, sol15m 500",
                )
                self.runtime.storage.insert_system_event(
                    utc_now(),
                    "telegram_scan_parse_error",
                    {"text": text, "errors": errors, "sent": ok, "reason": reason, "chat_id": chat_id, "user_id": user_id},
                )
                return

            analyses = self.runtime.analyze_symbols(
                symbols,
                push_to_telegram=False,
                timeframe_mode=timeframe,
                manual_total_usdt=manual_total_usdt,
            )
            payload = "\n\n".join(a.message for a in analyses)
            ok, reason = self._reply(chat_id, payload[:3800])
            self.runtime.storage.insert_system_event(
                utc_now(),
                "telegram_scan_command",
                {
                    "symbols": symbols,
                    "timeframe": timeframe,
                    "manual_total_usdt": manual_total_usdt,
                    "sent": ok,
                    "reason": reason,
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "role": role,
                },
            )
            return

        if lower.startswith("/result") or lower.startswith("/win") or lower.startswith("/loss"):
            self._handle_result_command(normalized, chat_id=chat_id, user_id=user_id, role=role)
            return

        if lower.startswith("/active"):
            now = utc_now()
            active_items = self.runtime.storage.list_active_advices(now=now)
            if not active_items:
                msg = "No active advices."
            else:
                lines = [f"Active advices ({len(active_items)}):"]
                for item in active_items:
                    side_name = "LONG" if str(item.side).upper() == "LONG" else "SHORT"
                    entry_text = f"{item.entry_trigger:.4f}" if item.entry_trigger is not None else "-"
                    short_id = str(item.advice_id).split("-")[-1]
                    lines.append(
                        f"{item.symbol} {side_name} | id={item.advice_id} | short={short_id} | entry {entry_text} | remaining ~{item.remaining_minutes}m"
                    )
                msg = "\n".join(lines)
            ok, reason = self._reply(chat_id, msg)
            self.runtime.storage.insert_system_event(
                utc_now(),
                "telegram_active_command",
                {"sent": ok, "reason": reason, "chat_id": chat_id, "user_id": user_id, "role": role},
            )
            return

        if lower.startswith("/alive") or lower.startswith("/ping"):
            msg = (
                "bot alive\n"
                f"time: {utc_now().isoformat(timespec='seconds')} UTC\n"
                f"mode: {self.runtime.mode.value}\n"
                f"watchlist: {', '.join(self.runtime.config.trading.symbols)}"
            )
            ok, reason = self._reply(chat_id, msg)
            self.runtime.storage.insert_system_event(
                utc_now(),
                "telegram_alive_command",
                {"sent": ok, "reason": reason, "chat_id": chat_id, "user_id": user_id, "role": role},
            )
            return

        if lower.startswith("/status"):
            msg = (
                f"mode: {self.runtime.mode.value}\n"
                f"exchange: {self.runtime.config.exchange.kind}\n"
                f"network: {self.runtime.config.hyperliquid.network}\n"
                f"hyperliquid_api: {self.runtime.config.hyperliquid.api_url}\n"
                f"auto_trade: {self.runtime.config.runtime.auto_trade_enabled and not self.runtime.config.runtime.advisory_only}\n"
                f"watchlist: {', '.join(self.runtime.config.trading.symbols)}\n"
                "positions: /positions\n"
                "active advices: /active\n"
                "alive check: /alive\n"
                "result report: /result <short_id|full_id|last> win 1.2\n"
                "quick report: /win BTCUSDT 0.8 or /loss ETHUSDT -0.6"
            )
            ok, reason = self._reply(chat_id, msg)
            self.runtime.storage.insert_system_event(
                utc_now(),
                "telegram_status_command",
                {"sent": ok, "reason": reason, "chat_id": chat_id, "user_id": user_id, "role": role},
            )
            return

        if lower.startswith("/positions"):
            self._handle_positions_command(chat_id=chat_id, user_id=user_id, role=role)
            return

        if lower.startswith("/help"):
            help_text = (
                "commands:\n"
                "/scan -> scan watchlist with default auto mode\n"
                "/scan BTCUSDT 1h -> scan one symbol with timeframe\n"
                "btc15m / eth1h / bnb15m / dot1h / solauto -> compact scan command\n"
                "/scan BTCUSDT 500 -> include total USDT budget for split sizing\n"
                "/positions -> show current open position lots\n"
                "/active -> show active unclosed advices\n"
                "/alive -> bot health check\n"
                "/status -> runtime status summary\n"
                "/net status|testnet|mainnet -> show or switch Hyperliquid network\n"
                "/result ABC123 win 1.2 -> report by short id\n"
                "/result last win 1.2 -> report latest active advice\n"
                "/result A-BTC-... win 1.2 -> report by full advice id\n"
                "/win SOLUSDT 0.8 -> quick win report\n"
                "/loss ETHUSDT -0.6 -> quick loss report\n"
                "/pause /resume /riskoff -> admin mode controls\n"
                "/closeall /killswitch -> admin only with /confirm CODE"
            )
            ok, reason = self._reply(chat_id, help_text)
            self.runtime.storage.insert_system_event(
                utc_now(),
                "telegram_help_command",
                {"sent": ok, "reason": reason, "chat_id": chat_id, "user_id": user_id, "role": role},
            )
            return

        ok, reason = self._reply(chat_id, "Unknown command. Use /help for command list.")
        self.runtime.storage.insert_system_event(
            utc_now(),
            "telegram_unknown_command",
            {"text": text, "sent": ok, "reason": reason, "chat_id": chat_id, "user_id": user_id, "role": role},
        )

    def ensure_menu_commands(self) -> tuple[bool, str]:
        if self.menu_sync_attempted:
            return (True, "already_attempted")
        self.menu_sync_attempted = True
        commands = self._menu_commands()
        ok, reason = self.notifier.set_my_commands(commands)
        self.runtime.storage.insert_system_event(
            utc_now(),
            "telegram_menu_sync",
            {"sent": ok, "reason": reason, "commands": [c["command"] for c in commands]},
        )
        return (ok, reason)

    @staticmethod
    def _menu_commands() -> list[dict[str, str]]:
        return [
            {"command": "scan", "description": "Scan symbols and show setup"},
            {"command": "positions", "description": "Show open positions"},
            {"command": "active", "description": "Show active advices"},
            {"command": "alive", "description": "Check bot is alive"},
            {"command": "status", "description": "Show system status"},
            {"command": "net", "description": "Show or switch network"},
            {"command": "help", "description": "Show command help"},
            {"command": "result", "description": "Report advice result by ID"},
            {"command": "pause", "description": "Pause new entries (admin)"},
            {"command": "resume", "description": "Resume running (admin)"},
            {"command": "riskoff", "description": "Risk-off mode (admin)"},
            {"command": "closeall", "description": "Close all positions (admin)"},
            {"command": "killswitch", "description": "Kill switch (admin)"},
        ]

    def _looks_like_compact_scan(self, lower_text: str) -> bool:
        tokens = [t for t in lower_text.replace("，", " ").replace(",", " ").split(" ") if t]
        if not tokens:
            return False
        if all(bool(COMPACT_PATTERN.match(token)) for token in tokens):
            return True
        if len(tokens) == 2 and COMPACT_PATTERN.match(tokens[0]):
            return self._parse_positive_amount(tokens[1]) is not None
        return False

    def _parse_symbols_and_timeframe(self, text: str) -> tuple[list[str], str, float | None, list[str]]:
        default_symbols = self.runtime.config.trading.symbols
        timeframe = "auto"
        manual_total_usdt: float | None = None
        errors: list[str] = []
        symbols: list[str] = []

        payload = text
        if payload.lower().startswith("/scan"):
            parts = payload.split(maxsplit=1)
            payload = parts[1] if len(parts) > 1 else ""
        payload = payload.replace("，", " ").replace(",", " ")
        tokens = [t.strip().lower() for t in payload.split(" ") if t.strip()]
        if not tokens:
            return (default_symbols, timeframe, manual_total_usdt, [])

        for token in tokens:
            compact = COMPACT_PATTERN.match(token)
            if compact:
                sym_short = compact.group(1)
                tf = compact.group(2)
                symbols.append(COMPACT_SYMBOLS[sym_short])
                if tf is not None:
                    timeframe = tf
                continue

            if token in SUPPORTED_TIMEFRAMES:
                timeframe = token
                continue

            amount = self._parse_positive_amount(token)
            if amount is not None:
                if manual_total_usdt is not None:
                    errors.append("duplicated_amount")
                else:
                    manual_total_usdt = amount
                continue

            mapped = self._normalize_symbol(token)
            if mapped is not None:
                symbols.append(mapped)
                continue

            errors.append(f"unknown_token:{token}")

        if not symbols:
            symbols = default_symbols

        unique_symbols: list[str] = []
        for symbol in symbols:
            if symbol not in unique_symbols:
                unique_symbols.append(symbol)

        if manual_total_usdt is not None and len(unique_symbols) != 1:
            errors.append("amount_requires_single_symbol")

        return (unique_symbols, timeframe, manual_total_usdt, errors)

    def _handle_positions_command(self, chat_id: str, user_id: str, role: str) -> None:
        pm = getattr(self.runtime, "position_manager", None)
        if pm is None or not hasattr(pm, "lots"):
            ok, reason = self._reply(chat_id, "Position manager unavailable in current runtime.")
            self.runtime.storage.insert_system_event(
                utc_now(),
                "telegram_positions_unavailable",
                {"sent": ok, "reason": reason, "chat_id": chat_id, "user_id": user_id, "role": role},
            )
            return

        active = [lot for lot in pm.lots if getattr(lot, "active", False) or not getattr(lot, "exit_executed", True)]
        if not active:
            ok, reason = self._reply(chat_id, "No open positions.")
            self.runtime.storage.insert_system_event(
                utc_now(),
                "telegram_positions_command",
                {"open_positions": 0, "sent": ok, "reason": reason, "chat_id": chat_id, "user_id": user_id, "role": role},
            )
            return

        lines = [f"Open positions ({len(active)} lots):"]
        for lot in active:
            tp = f"{lot.take_profit:.6f}" if lot.take_profit is not None else "-"
            opened = lot.opened_at.isoformat(timespec="seconds") if getattr(lot, "opened_at", None) else "-"
            lines.append(
                f"{lot.symbol} {lot.side.value} {lot.kind.value} | qty={lot.quantity:.6f} "
                f"entry={lot.avg_entry:.6f} stop={lot.current_stop:.6f} tp={tp} "
                f"trail={lot.trailing_armed} be={lot.breakeven_armed} "
                f"tf={getattr(lot, 'entry_timeframe', '-')} bars15={getattr(lot, 'bars_held_15m', 0)} "
                f"bars1h={getattr(lot, 'bars_held_1h', 0)} opened={opened} exit={getattr(lot, 'exit_reason', '') or '-'}"
            )
        ok, reason = self._reply(chat_id, "\n".join(lines))
        self.runtime.storage.insert_system_event(
            utc_now(),
            "telegram_positions_command",
            {"open_positions": len(active), "sent": ok, "reason": reason, "chat_id": chat_id, "user_id": user_id, "role": role},
        )

    def _handle_result_command(self, text: str, chat_id: str, user_id: str, role: str) -> None:
        parsed, error = self._parse_result_command(text)
        if error is not None or parsed is None:
            ok, reason = self._reply(
                chat_id,
                "Invalid result command.\n"
                "Try: /result ABC123 win 1.2 or /result last win 1.2\n"
                "Quick: /win BTCUSDT 0.9 or /loss ETHUSDT -0.7",
            )
            self.runtime.storage.insert_system_event(
                utc_now(),
                "telegram_result_parse_error",
                {"text": text, "error": error or "invalid_result_command", "sent": ok, "reason": reason, "chat_id": chat_id, "user_id": user_id, "role": role},
            )
            return

        advice_id, symbol, outcome, pnl_pct, note = parsed
        now = utc_now()

        if advice_id is not None:
            resolved_advice_id, resolve_error = self._resolve_advice_target(advice_id, now=now)
            if resolve_error is not None or resolved_advice_id is None:
                ok, reason = self._reply(chat_id, resolve_error or "Advice lookup failed")
                self.runtime.storage.insert_system_event(
                    now,
                    "telegram_result_advice_resolve_error",
                    {"target": advice_id, "error": resolve_error or "resolve_failed", "sent": ok, "reason": reason, "chat_id": chat_id, "user_id": user_id, "role": role},
                )
                return
            advice = self.runtime.storage.get_advice_record(resolved_advice_id)
            if advice is None:
                ok, reason = self._reply(chat_id, f"AdviceID not found: {resolved_advice_id}")
                self.runtime.storage.insert_system_event(
                    now,
                    "telegram_result_advice_not_found",
                    {"advice_id": resolved_advice_id, "sent": ok, "reason": reason, "chat_id": chat_id, "user_id": user_id, "role": role},
                )
                return
            if self.runtime.storage.has_feedback_for_advice(resolved_advice_id):
                ok, reason = self._reply(chat_id, f"Already reported: {resolved_advice_id}")
                self.runtime.storage.insert_system_event(
                    now,
                    "telegram_result_duplicate_advice",
                    {"advice_id": resolved_advice_id, "sent": ok, "reason": reason, "chat_id": chat_id, "user_id": user_id, "role": role},
                )
                return
            advice_id = resolved_advice_id
            symbol = str(advice["symbol"])

        assert symbol is not None
        self.runtime.storage.insert_trade_feedback(
            ts=now,
            advice_id=advice_id,
            symbol=symbol,
            outcome=outcome,
            pnl_pct=pnl_pct,
            note=note,
            payload={"source": "telegram", "raw_text": text, "chat_id": chat_id, "user_id": user_id, "role": role},
        )
        if advice_id is not None:
            self.runtime.storage.close_advice_record(advice_id=advice_id, closed_ts=now)

        self.runtime.storage.insert_operator_command(
            ts=now,
            command="trade_result",
            payload={"advice_id": advice_id, "symbol": symbol, "outcome": outcome, "pnl_pct": pnl_pct, "note": note, "chat_id": chat_id, "user_id": user_id, "role": role},
        )

        stats_all = self.runtime.storage.trade_feedback_stats()
        stats_symbol = self.runtime.storage.trade_feedback_stats(symbol=symbol)
        pnl_part = f"{pnl_pct:.2f}%" if pnl_pct is not None else "N/A"
        advice_part = f"\nAdviceID: {advice_id}" if advice_id is not None else ""
        msg = (
            f"Recorded: {symbol} {outcome}, PnL={pnl_part}{advice_part}\n"
            f"{symbol} stats: {stats_symbol['wins']}/{stats_symbol['total']} wins, win rate {stats_symbol['win_rate_pct']:.1f}%\n"
            f"Global stats: {stats_all['wins']}/{stats_all['total']} wins, win rate {stats_all['win_rate_pct']:.1f}%"
        )
        ok, reason = self._reply(chat_id, msg)
        self.runtime.storage.insert_system_event(
            now,
            "telegram_result_recorded",
            {
                "advice_id": advice_id,
                "symbol": symbol,
                "outcome": outcome,
                "pnl_pct": pnl_pct,
                "note": note,
                "sent": ok,
                "reason": reason,
                "chat_id": chat_id,
                "user_id": user_id,
                "role": role,
            },
        )

    def _request_danger_confirm(self, action: str, chat_id: str, user_id: str) -> None:
        code = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
        expires_at = utc_now() + timedelta(seconds=max(15, self._confirm_ttl_seconds()))
        self.pending_confirms[self._confirm_key(chat_id, user_id)] = PendingConfirm(action=action, code=code, expires_at=expires_at)
        self._reply(chat_id, f"Confirm required for {action}. Send /confirm {code} within {self._confirm_ttl_seconds()}s.")
        self.runtime.storage.insert_system_event(
            utc_now(),
            "telegram_danger_confirm_requested",
            {"action": action, "chat_id": chat_id, "user_id": user_id, "expires_at": expires_at.isoformat()},
        )

    def _handle_confirm_command(self, text: str, chat_id: str, user_id: str) -> None:
        parts = [p for p in text.strip().split(" ") if p]
        if len(parts) < 2:
            self._reply(chat_id, "Usage: /confirm CODE")
            return
        code = parts[1].strip().upper()
        key = self._confirm_key(chat_id, user_id)
        pending = self.pending_confirms.get(key)
        if pending is None:
            self._reply(chat_id, "No pending dangerous action.")
            return
        if utc_now() > pending.expires_at:
            self.pending_confirms.pop(key, None)
            self._reply(chat_id, "Confirmation expired. Re-run command.")
            return
        if code != pending.code:
            self._reply(chat_id, "Invalid confirmation code.")
            return
        self.pending_confirms.pop(key, None)
        self._execute_confirmed_action(action=pending.action, chat_id=chat_id, user_id=user_id)

    def _execute_confirmed_action(self, action: str, chat_id: str, user_id: str) -> None:
        if action == "/closeall":
            self._close_all_positions()
            if self.runtime.mode != SystemMode.KILLED:
                self.runtime.mode = SystemMode.PAUSED
            msg = "closeall executed. Mode set to PAUSED."
            event = "telegram_closeall_executed"
        elif action == "/killswitch":
            self._close_all_positions()
            self.runtime.mode = SystemMode.KILLED
            msg = "killswitch executed. Mode set to KILLED."
            event = "telegram_killswitch_executed"
        elif action.startswith("/net "):
            network = action.split(" ", maxsplit=1)[1].strip().lower()
            ok, result = self.runtime.switch_hyperliquid_network(network)
            if ok:
                msg = f"Hyperliquid network switched to {result}."
                event = "telegram_network_switched"
            else:
                msg = f"Network switch failed: {result}"
                event = "telegram_network_switch_failed"
        else:
            msg = f"Unknown confirmed action: {action}"
            event = "telegram_confirm_unknown_action"
        ok, reason = self._reply(chat_id, msg)
        self.runtime.storage.insert_system_event(
            utc_now(),
            event,
            {"action": action, "chat_id": chat_id, "user_id": user_id, "sent": ok, "reason": reason},
        )

    def _set_mode(self, mode: SystemMode, chat_id: str, event: str) -> None:
        self.runtime.mode = mode
        ok, reason = self._reply(chat_id, f"Mode -> {mode.value}")
        self.runtime.storage.insert_system_event(utc_now(), event, {"mode": mode.value, "sent": ok, "reason": reason, "chat_id": chat_id})

    def _handle_net_command(self, text: str, chat_id: str, user_id: str, role: str) -> None:
        parts = [p for p in text.strip().split(" ") if p]
        if len(parts) < 2:
            self._reply(chat_id, self._network_status_text())
            self.runtime.storage.insert_system_event(
                utc_now(),
                "telegram_network_status_command",
                {"chat_id": chat_id, "user_id": user_id, "role": role, "network": self.runtime.config.hyperliquid.network},
            )
            return

        target = parts[1].strip().lower()
        if target == "status":
            self._reply(chat_id, self._network_status_text())
            self.runtime.storage.insert_system_event(
                utc_now(),
                "telegram_network_status_command",
                {"chat_id": chat_id, "user_id": user_id, "role": role, "network": self.runtime.config.hyperliquid.network},
            )
            return

        if role != "admin":
            self._reply(chat_id, "Permission denied for network switch. Ask an admin.")
            self.runtime.storage.insert_system_event(
                utc_now(),
                "telegram_network_switch_denied",
                {"chat_id": chat_id, "user_id": user_id, "role": role, "target": target},
            )
            return

        if target not in {"testnet", "mainnet"}:
            self._reply(chat_id, "Usage: /net status|testnet|mainnet")
            return

        current = self.runtime.config.hyperliquid.network.strip().lower()
        if target == current:
            self._reply(chat_id, self._network_status_text())
            return

        if target == "mainnet":
            self._request_danger_confirm(action="/net mainnet", chat_id=chat_id, user_id=user_id)
            return

        ok, result = self.runtime.switch_hyperliquid_network("testnet")
        if ok:
            self._reply(chat_id, f"Hyperliquid network switched to {result}.")
        else:
            self._reply(chat_id, f"Network switch failed: {result}")
        self.runtime.storage.insert_system_event(
            utc_now(),
            "telegram_network_switch_command",
            {"chat_id": chat_id, "user_id": user_id, "role": role, "target": target, "ok": ok, "result": result},
        )

    def _network_status_text(self) -> str:
        cfg = self.runtime.config.hyperliquid
        return (
            f"Hyperliquid network: {cfg.network}\n"
            f"API: {cfg.api_url}\n"
            f"WS: {cfg.ws_url}\n"
            "Use /net testnet or /net mainnet (mainnet requires confirmation and admin rights)."
        )

    def _close_all_positions(self) -> None:
        pm = getattr(self.runtime, "position_manager", None)
        if pm is not None and hasattr(pm, "close_all"):
            try:
                pm.close_all()
            except Exception:
                pass
        engine = getattr(self.runtime, "execution_engine", None)
        if engine is not None and hasattr(engine, "close_all"):
            try:
                live_symbols = sorted({
                    lot.symbol
                    for lot in getattr(pm, "lots", [])
                    if getattr(lot, "active", False) or not getattr(lot, "exit_executed", True)
                })
                engine.close_all(live_symbols or self.runtime.config.trading.symbols)
            except Exception:
                pass
        flush = getattr(self.runtime, "_flush_pending_position_exits", None)
        if callable(flush):
            live_symbols = sorted({
                lot.symbol
                for lot in getattr(pm, "lots", [])
                if getattr(lot, "active", False) or not getattr(lot, "exit_executed", True)
            })
            for symbol in live_symbols or self.runtime.config.trading.symbols:
                try:
                    flush(symbol, None)
                except Exception:
                    pass
        self.runtime._refresh_account_for_symbol(symbol="")

    def _resolve_advice_target(self, target: str, now: datetime) -> tuple[str | None, str | None]:
        token = target.strip()
        if not token:
            return (None, "Advice target cannot be empty.")
        if token.lower() == "last":
            latest = self.runtime.storage.get_latest_active_advice(now=now)
            if latest is None:
                return (None, "No active advice found for token 'last'.")
            return (latest.advice_id, None)

        if token.upper().startswith("A-"):
            return (token, None)

        compact = token.upper()
        if re.fullmatch(r"[A-Z0-9]{4,10}", compact):
            open_hits = self.runtime.storage.get_advice_ids_by_suffix(compact, status="OPEN")
            if len(open_hits) == 1:
                return (open_hits[0], None)
            if len(open_hits) > 1:
                return (None, f"Short ID {compact} matches multiple OPEN advices. Please use full AdviceID.")

            any_hits = self.runtime.storage.get_advice_ids_by_suffix(compact, status=None)
            if len(any_hits) == 1:
                return (any_hits[0], None)
            if len(any_hits) > 1:
                return (None, f"Short ID {compact} matches multiple historical advices. Please use full AdviceID.")
            return (None, f"Short ID {compact} not found.")

        return (token, None)

    def _parse_result_command(self, text: str) -> tuple[tuple[str | None, str | None, str, float | None, str] | None, str | None]:
        normalized = text.strip()
        tokens = [t for t in normalized.replace("，", " ").split(" ") if t]
        if len(tokens) < 2:
            return (None, "not_enough_tokens")
        cmd = tokens[0].lower()

        if cmd in {"/win", "/loss"}:
            symbol = self._normalize_symbol(tokens[1])
            if symbol is None:
                return (None, "invalid_symbol")
            outcome = "WIN" if cmd == "/win" else "LOSS"
            pnl_pct = self._parse_maybe_signed_float(tokens[2]) if len(tokens) >= 3 else None
            note = " ".join(tokens[3:]) if len(tokens) >= 4 else ""
            return ((None, symbol, outcome, pnl_pct, note), None)

        if cmd != "/result":
            return (None, "unknown_result_command")
        if len(tokens) < 3:
            return (None, "missing_target_or_outcome")

        target = tokens[1]
        outcome = self._parse_outcome_token(tokens[2])
        if outcome is None:
            return (None, "invalid_outcome")

        advice_id: str | None = None
        symbol: str | None = self._normalize_symbol(target)
        if symbol is None:
            advice_id = target

        pnl_pct: float | None = None
        note_start = 3
        if len(tokens) >= 4:
            maybe = self._parse_maybe_signed_float(tokens[3])
            if maybe is not None:
                pnl_pct = maybe
                note_start = 4
        note = " ".join(tokens[note_start:]) if len(tokens) > note_start else ""
        return ((advice_id, symbol, outcome, pnl_pct, note), None)

    @staticmethod
    def _normalize_symbol(token: str) -> str | None:
        t = token.upper()
        if t in {"BTC", "BTCUSDT"}:
            return "BTCUSDT"
        if t in {"ETH", "ETHUSDT"}:
            return "ETHUSDT"
        if t in {"BNB", "BNBUSDT"}:
            return "BNBUSDT"
        if t in {"DOT", "DOTUSDT"}:
            return "DOTUSDT"
        if t in {"SOL", "SOLUSDT"}:
            return "SOLUSDT"
        return None

    @staticmethod
    def _parse_positive_amount(token: str) -> float | None:
        try:
            value = float(token)
        except ValueError:
            return None
        if value <= 0:
            return None
        return value

    @staticmethod
    def _parse_outcome_token(token: str) -> str | None:
        t = token.strip().lower()
        if t in {"win", "w", "profit", "盈利", "赢"}:
            return "WIN"
        if t in {"loss", "l", "lose", "亏损", "亏"}:
            return "LOSS"
        return None

    @staticmethod
    def _parse_maybe_signed_float(token: str) -> float | None:
        try:
            return float(token)
        except ValueError:
            return None

    def _role_allows(self, role: str, command_key: str) -> bool:
        if role == "admin":
            return command_key in ADMIN_COMMANDS or command_key == "/scan_compact"
        if role == "trader":
            return command_key in TRADER_COMMANDS or command_key == "/scan_compact"
        return command_key in VIEWER_COMMANDS or command_key == "/scan_compact"

    def _resolve_role(self, user_id: str) -> str | None:
        cfg = self.notifier.config
        admin_ids = set(cfg.admin_user_ids)
        trader_ids = set(cfg.trader_user_ids)
        viewer_ids = set(cfg.viewer_user_ids)
        has_any_role_whitelist = bool(admin_ids or trader_ids or viewer_ids)

        if user_id in admin_ids:
            return "admin"
        if user_id in trader_ids:
            return "trader"
        if user_id in viewer_ids:
            return "viewer"
        if has_any_role_whitelist:
            return None
        return "admin"

    def _is_allowed_chat(self, chat_id: str) -> bool:
        cfg = self.notifier.config
        allowed = {item for item in cfg.allowed_chat_ids if item}
        if cfg.chat_id:
            allowed.add(cfg.chat_id)
        if not allowed:
            return True
        return chat_id in allowed

    @staticmethod
    def _command_key(lower_text: str) -> str:
        token = lower_text.split(" ")[0] if lower_text else ""
        if token.startswith("/"):
            return token
        if COMPACT_PATTERN.match(token):
            return "/scan_compact"
        return token

    def _reply(self, chat_id: str, text: str) -> tuple[bool, str]:
        target_chat = chat_id or self.notifier.config.chat_id
        if not target_chat:
            return self.notifier.send_text(text)
        return self.notifier.send_text_to_chat(target_chat, text)

    def _confirm_ttl_seconds(self) -> int:
        return max(15, int(self.notifier.config.confirm_ttl_seconds))

    @staticmethod
    def _confirm_key(chat_id: str, user_id: str) -> str:
        return f"{chat_id}:{user_id}"

    def _load_offset(self) -> int | None:
        if not self.offset_path.exists():
            return None
        content = self.offset_path.read_text(encoding="utf-8").strip()
        if not content:
            return None
        try:
            return int(content)
        except ValueError:
            return None

    def _save_offset(self, offset: int) -> None:
        self.offset_path.write_text(str(offset), encoding="utf-8")
