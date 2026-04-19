from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from .runtime import TradingRuntime
from .telegram_notify import TelegramNotifier
from .time_utils import utc_now

SUPPORTED_TIMEFRAMES = {"15m", "1h", "hybrid", "auto"}
COMPACT_SYMBOLS = {
    "btc": "BTCUSDT",
    "eth": "ETHUSDT",
    "bnb": "BNBUSDT",
    "dot": "DOTUSDT",
    "sol": "SOLUSDT",
}
COMPACT_PATTERN = re.compile(r"^(btc|eth|bnb|dot|sol)(15m|1h|hybrid|auto)?$")


@dataclass(slots=True)
class TelegramCommandBot:
    runtime: TradingRuntime
    notifier: TelegramNotifier
    offset_path: Path

    @classmethod
    def from_runtime(cls, runtime: TradingRuntime) -> "TelegramCommandBot":
        offset_path = Path(runtime.config.runtime.telegram_offset_path)
        offset_path.parent.mkdir(parents=True, exist_ok=True)
        return cls(runtime=runtime, notifier=runtime.notifier, offset_path=offset_path)

    def run_once(self, timeout_seconds: int = 25) -> str:
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
            chat_id = str(chat.get("id", ""))
            if self.notifier.config.chat_id and chat_id != str(self.notifier.config.chat_id):
                continue
            text = str(message.get("text", "")).strip()
            if not text:
                continue
            self._handle_text_command(text)
            handled += 1

        self._save_offset(max_update_id)
        return f"handled:{handled}"

    def _handle_text_command(self, text: str) -> None:
        normalized = text.strip()
        lower = normalized.lower()

        if lower.startswith("/scan") or self._looks_like_compact_scan(lower):
            symbols, timeframe, manual_total_usdt, errors = self._parse_symbols_and_timeframe(normalized)
            if errors:
                ok, reason = self.notifier.send_text(
                    "命令格式不正确。\n"
                    "示例: /scan, btc15m, bnb1h, dotauto, /scan BTCUSDT 500, sol15m 500"
                )
                self.runtime.storage.insert_system_event(
                    utc_now(),
                    "telegram_scan_parse_error",
                    {"text": text, "errors": errors, "sent": ok, "reason": reason},
                )
                return

            analyses = self.runtime.analyze_symbols(
                symbols,
                push_to_telegram=False,
                timeframe_mode=timeframe,
                manual_total_usdt=manual_total_usdt,
            )
            payload = "\n\n".join(a.message for a in analyses)
            ok, reason = self.notifier.send_text(payload[:3800])
            self.runtime.storage.insert_system_event(
                utc_now(),
                "telegram_scan_command",
                {"symbols": symbols, "timeframe": timeframe, "manual_total_usdt": manual_total_usdt, "sent": ok, "reason": reason},
            )
            return

        if lower.startswith("/result") or lower.startswith("/win") or lower.startswith("/loss"):
            self._handle_result_command(normalized)
            return

        if lower.startswith("/status"):
            msg = (
                f"系统模式: {self.runtime.mode.value}\n"
                f"分析白名单: {', '.join(self.runtime.config.trading.symbols)}\n"
                "建议回报(推荐): /result <AdviceID> win 1.2\n"
                "快速回报: /win BTCUSDT 0.8 或 /loss ETHUSDT -0.6"
            )
            ok, reason = self.notifier.send_text(msg)
            self.runtime.storage.insert_system_event(utc_now(), "telegram_status_command", {"sent": ok, "reason": reason})
            return

        if lower.startswith("/help"):
            help_text = (
                "可用命令:\n"
                "/scan -> 默认扫描白名单(BTC/ETH/BNB/DOT/SOL)，默认auto\n"
                "/scan BTCUSDT 1h -> 指定币和周期\n"
                "btc15m / eth1h / bnb15m / dot1h / solauto -> 短命令\n"
                "/scan BTCUSDT 500 -> 输入总预算USDT，回传主副仓数量\n"
                "/result <AdviceID> win 1.2 -> 按建议ID回报(推荐)\n"
                "/win SOLUSDT 0.8 -> 仅按币种快速记录盈利\n"
                "/loss ETHUSDT -0.6 -> 仅按币种快速记录亏损\n"
                "/status -> 查看系统状态"
            )
            ok, reason = self.notifier.send_text(help_text)
            self.runtime.storage.insert_system_event(utc_now(), "telegram_help_command", {"sent": ok, "reason": reason})
            return

        ok, reason = self.notifier.send_text("未识别命令。用 /help 查看可用指令。")
        self.runtime.storage.insert_system_event(utc_now(), "telegram_unknown_command", {"text": text, "sent": ok, "reason": reason})

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

    def _handle_result_command(self, text: str) -> None:
        parsed, error = self._parse_result_command(text)
        if error is not None or parsed is None:
            ok, reason = self.notifier.send_text(
                "结果命令格式不正确。\n"
                "推荐: /result A-BTC-1H-20260420153012-ABC123 win 1.2\n"
                "快速: /win BTCUSDT 0.9 或 /loss ETHUSDT -0.7"
            )
            self.runtime.storage.insert_system_event(
                utc_now(),
                "telegram_result_parse_error",
                {"text": text, "error": error or "invalid_result_command", "sent": ok, "reason": reason},
            )
            return

        advice_id, symbol, outcome, pnl_pct, note = parsed
        now = utc_now()

        if advice_id is not None:
            advice = self.runtime.storage.get_advice_record(advice_id)
            if advice is None:
                ok, reason = self.notifier.send_text(f"AdviceID不存在: {advice_id}")
                self.runtime.storage.insert_system_event(
                    now,
                    "telegram_result_advice_not_found",
                    {"advice_id": advice_id, "sent": ok, "reason": reason},
                )
                return
            if self.runtime.storage.has_feedback_for_advice(advice_id):
                ok, reason = self.notifier.send_text(f"该AdviceID已回报过: {advice_id}")
                self.runtime.storage.insert_system_event(
                    now,
                    "telegram_result_duplicate_advice",
                    {"advice_id": advice_id, "sent": ok, "reason": reason},
                )
                return
            symbol = str(advice["symbol"])

        assert symbol is not None
        self.runtime.storage.insert_trade_feedback(
            ts=now,
            advice_id=advice_id,
            symbol=symbol,
            outcome=outcome,
            pnl_pct=pnl_pct,
            note=note,
            payload={"source": "telegram", "raw_text": text},
        )
        if advice_id is not None:
            self.runtime.storage.close_advice_record(advice_id=advice_id, closed_ts=now)

        self.runtime.storage.insert_operator_command(
            ts=now,
            command="trade_result",
            payload={"advice_id": advice_id, "symbol": symbol, "outcome": outcome, "pnl_pct": pnl_pct, "note": note},
        )

        stats_all = self.runtime.storage.trade_feedback_stats()
        stats_symbol = self.runtime.storage.trade_feedback_stats(symbol=symbol)
        pnl_part = f"{pnl_pct:.2f}%" if pnl_pct is not None else "未填写"
        advice_part = f"\nAdviceID: {advice_id}" if advice_id is not None else ""
        msg = (
            f"已记录: {symbol} {outcome}，本笔PnL={pnl_part}{advice_part}\n"
            f"{symbol}统计: {stats_symbol['wins']}/{stats_symbol['total']} 胜，胜率{stats_symbol['win_rate_pct']:.1f}%\n"
            f"全局统计: {stats_all['wins']}/{stats_all['total']} 胜，胜率{stats_all['win_rate_pct']:.1f}%"
        )
        ok, reason = self.notifier.send_text(msg)
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
            },
        )

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
