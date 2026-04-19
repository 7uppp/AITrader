from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

from .advisory import advisory_to_telegram_text, build_trade_advisory
from .audit import config_hash
from .binance_market_data import BinanceMarketDataClient
from .config import AppConfig
from .market_data import MarketDataPolicy, MarketDataValidator
from .risk import RiskEngine
from .storage import Storage
from .strategy import SignalEngine, TimeframeMode
from .telegram_notify import TelegramNotifier
from .time_utils import utc_now
from .types import AccountState, MarketSnapshot, SignalIntent, SystemMode


SIGNAL_REASON_MAP: dict[str, str] = {
    "market_stale": "行情数据过期",
    "insufficient_history": "K线历史长度不足",
    "atr_percentile_out_of_range": "波动率分位不在30%-75%区间",
    "trend_not_confirmed": "4H趋势未确认",
    "long_disabled": "当前配置禁用做多",
    "short_disabled": "当前配置禁用做空",
    "pullback_not_confirmed": "1H回踩结构未确认",
    "breakout_or_volume_not_confirmed": "15m突破或量能条件未确认",
    "atr_invalid": "ATR计算无效",
    "risk_distance_invalid": "止损距离无效",
}

RISK_REASON_MAP: dict[str, str] = {
    "system:killed": "系统处于Kill状态",
    "system:paused": "系统处于暂停状态",
    "system:risk_off": "系统处于只减仓风险模式",
    "market:stale": "市场数据过期",
    "risk:daily_limit_hit": "触发日亏损上限",
    "risk:weekly_limit_hit": "触发周亏损上限",
    "risk:max_drawdown_hit": "触发最大回撤停机",
    "risk:consecutive_losses_hit": "触发连续亏损停机",
    "risk:max_open_positions_hit": "达到最大持仓数",
    "risk:max_open_risk_hit": "达到总开放风险上限",
    "risk:free_margin_low": "可用保证金不足",
    "risk:invalid_stop_distance": "止损距离无效",
    "risk:extreme_funding": "资金费率过热",
    "risk:oi_spike": "OI变化过快",
    "risk:spread_too_wide": "点差过大",
    "risk:slippage_too_high": "预估滑点过高",
    "risk:leverage_above_hard_limit": "杠杆超过硬上限",
    "risk:liq_buffer_too_low": "清算缓冲不足",
    "risk:liq_distance_below_atr_rule": "清算距离不满足ATR规则",
    "risk:liq_distance_below_stop_ratio": "清算距离不满足止损倍数规则",
    "risk:position_size_zero": "可下单数量为0",
    "risk:symbol_exposure_hit": "单币敞口达到上限",
    "risk:open_risk_after_trade_exceeds_limit": "下单后总风险将超上限",
}


@dataclass(slots=True)
class RuntimeResult:
    processed_symbols: int = 0
    signals: int = 0
    approved: int = 0
    rejected: int = 0
    advisories_sent: int = 0
    advisories_generated: int = 0


@dataclass(slots=True)
class SymbolAnalysis:
    symbol: str
    suitable: bool
    message: str
    reasons: list[str]
    timeframe_mode: str = "hybrid"


@dataclass(slots=True)
class TradingRuntime:
    config: AppConfig
    data_client: BinanceMarketDataClient
    signal_engine: SignalEngine
    risk_engine: RiskEngine
    notifier: TelegramNotifier
    market_validator: MarketDataValidator
    storage: Storage
    mode: SystemMode
    account: AccountState

    @classmethod
    def from_config(cls, cfg: AppConfig) -> "TradingRuntime":
        storage = Storage(Path(cfg.runtime.database_path))
        storage.path.parent.mkdir(parents=True, exist_ok=True)
        storage.init_schema()
        runtime = cls(
            config=cfg,
            data_client=BinanceMarketDataClient(cfg.binance),
            signal_engine=SignalEngine(cfg.trading, cfg.strategy),
            risk_engine=RiskEngine(cfg.trading, cfg.risk),
            notifier=TelegramNotifier(cfg.telegram),
            market_validator=MarketDataValidator(MarketDataPolicy()),
            storage=storage,
            mode=cfg.system.mode,
            account=AccountState(
                equity=10_000.0,
                free_margin=8_000.0,
                daily_pnl_pct=0.0,
                weekly_pnl_pct=0.0,
                drawdown_pct=0.0,
                consecutive_losses=0,
                open_positions=0,
                open_risk_pct=0.0,
                symbol_notional_pct=0.0,
            ),
        )
        runtime._record_config_version()
        if not cfg.runtime.advisory_only:
            runtime.storage.insert_system_event(
                utc_now(),
                "runtime_guardrail",
                {"message": "advisory_only=false configured, but runtime remains advisory-only by design"},
            )
        return runtime

    def run_cycle(self) -> RuntimeResult:
        analyses = self.analyze_symbols(self.config.trading.symbols, push_to_telegram=True, timeframe_mode="auto")
        result = RuntimeResult(processed_symbols=len(analyses))
        for analysis in analyses:
            if analysis.suitable:
                result.signals += 1
                result.approved += 1
                result.advisories_generated += 1
                if "[telegram:sent]" in analysis.reasons:
                    result.advisories_sent += 1
            else:
                result.rejected += 1
        return result

    def analyze_symbols(
        self,
        symbols: list[str],
        push_to_telegram: bool = False,
        timeframe_mode: TimeframeMode | str = "auto",
        manual_total_usdt: float | None = None,
    ) -> list[SymbolAnalysis]:
        outputs: list[SymbolAnalysis] = []
        allowed = {sym.upper() for sym in self.config.trading.symbols}
        for raw_symbol in symbols:
            symbol = raw_symbol.upper().strip()
            if symbol not in allowed:
                outputs.append(
                    SymbolAnalysis(
                        symbol=symbol,
                        suitable=False,
                        message=f"[不适合] {symbol}\n原因: 不在白名单，仅支持: {', '.join(sorted(allowed))}",
                        reasons=["symbol_not_allowed"],
                        timeframe_mode=timeframe_mode,
                    )
                )
                continue
            outputs.append(
                self._analyze_one_symbol(
                    symbol,
                    push_to_telegram=push_to_telegram,
                    timeframe_mode=timeframe_mode,
                    manual_total_usdt=manual_total_usdt,
                )
            )
        return outputs

    def _analyze_one_symbol(
        self,
        symbol: str,
        push_to_telegram: bool = False,
        timeframe_mode: TimeframeMode | str = "auto",
        manual_total_usdt: float | None = None,
    ) -> SymbolAnalysis:
        try:
            snapshot = self.data_client.fetch_snapshot(symbol)
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code if exc.response is not None else 0
            if status_code == 451:
                message = (
                    f"[不适合] {symbol}\n"
                    "原因: Binance API 返回451，当前VPS出口IP所在地区或机房被限制。\n"
                    "处理: 更换到允许的VPS区域，或改用合规的数据源后再运行。"
                )
                self.storage.insert_system_event(
                    utc_now(),
                    "binance_region_restricted",
                    {"symbol": symbol, "status_code": status_code, "url": str(exc.request.url) if exc.request else None},
                )
                if push_to_telegram:
                    ok, reason = self.notifier.send_text(message)
                    self.storage.insert_system_event(
                        utc_now(),
                        "telegram_region_restricted_notice",
                        {"symbol": symbol, "sent": ok, "reason": reason},
                    )
                return SymbolAnalysis(
                    symbol=symbol,
                    suitable=False,
                    message=message,
                    reasons=["market_region_restricted"],
                    timeframe_mode=timeframe_mode if isinstance(timeframe_mode, str) else "auto",
                )
            raise
        except httpx.RequestError as exc:
            message = (
                f"[不适合] {symbol}\n"
                f"原因: 行情请求失败({type(exc).__name__})，请检查网络或VPS出口。"
            )
            self.storage.insert_system_event(
                utc_now(),
                "binance_request_error",
                {"symbol": symbol, "error": type(exc).__name__, "message": str(exc)},
            )
            if push_to_telegram:
                ok, reason = self.notifier.send_text(message)
                self.storage.insert_system_event(
                    utc_now(),
                    "telegram_request_error_notice",
                    {"symbol": symbol, "sent": ok, "reason": reason},
                )
            return SymbolAnalysis(
                symbol=symbol,
                suitable=False,
                message=message,
                reasons=["market_request_error"],
                timeframe_mode=timeframe_mode if isinstance(timeframe_mode, str) else "auto",
            )
        valid, reasons = self.market_validator.validate(snapshot, now=utc_now())
        if not valid:
            snapshot.is_stale = "stale_snapshot" in reasons
            self.storage.insert_system_event(utc_now(), "market_validation_failed", {"symbol": symbol, "reasons": reasons})
        self.storage.insert_market_snapshot(snapshot.ts, symbol, snapshot)

        self._refresh_account_for_symbol(symbol)
        selected_mode, signal, signal_failed = self._pick_signal(snapshot, timeframe_mode)
        if signal is None:
            detail = self._format_signal_reasons_autoaware(signal_failed)
            message = (
                f"[不适合] {symbol}\n"
                f"判定框架: {selected_mode}\n"
                f"原因: {detail}\n"
                f"Mark/Funding/OI: {snapshot.mark_price:.4f} / {snapshot.funding_rate_pct:.4f}% / {snapshot.oi_change_1h_pct:.2f}%\n"
                "主副仓规则: 主仓60%先止盈(+1R)，副仓40%趋势跟随。"
            )
            sent = False
            if push_to_telegram and self.config.telegram.send_rejections:
                ok, reason = self.notifier.send_text(message)
                sent = ok
                self.storage.insert_system_event(utc_now(), "telegram_unsuitable_notice", {"symbol": symbol, "sent": ok, "reason": reason})
            reason_codes = signal_failed if signal_failed else ["signal_not_ready"]
            return SymbolAnalysis(
                symbol=symbol,
                suitable=False,
                message=message,
                reasons=[*reason_codes, "[telegram:sent]" if sent else "[telegram:not_sent]"],
                timeframe_mode=selected_mode,
            )

        self.storage.insert_signal_intent(snapshot.ts, symbol, signal.side.value, signal)
        decision = self.risk_engine.assess(signal, snapshot, self.account, self.mode)
        self.storage.insert_risk_decision(snapshot.ts, symbol, decision.approved, decision)
        if not decision.approved:
            reason_text = self._format_risk_reasons(decision.reason_codes)
            message = (
                f"[不适合] {symbol}\n"
                f"判定框架: {selected_mode}\n"
                f"原因: 风控拒绝({reason_text})\n"
                f"Mark/Funding/OI: {snapshot.mark_price:.4f} / {snapshot.funding_rate_pct:.4f}% / {snapshot.oi_change_1h_pct:.2f}%"
            )
            sent = False
            if push_to_telegram and self.config.telegram.send_rejections:
                ok, reason = self.notifier.send_text(message)
                sent = ok
                self.storage.insert_system_event(utc_now(), "telegram_rejection_notice", {"symbol": symbol, "sent": ok, "reason": reason})
            return SymbolAnalysis(
                symbol=symbol,
                suitable=False,
                message=message,
                reasons=[*decision.reason_codes, "[telegram:sent]" if sent else "[telegram:not_sent]"],
                timeframe_mode=selected_mode,
            )

        atr_15m = self._atr_from_candles(snapshot.candles_15m)
        advisory = build_trade_advisory(
            self.config,
            snapshot,
            signal,
            decision,
            atr_15m=atr_15m,
            manual_total_usdt=manual_total_usdt,
        )
        if push_to_telegram and self.storage.recent_advice_exists(
            symbol=symbol,
            side=signal.side.value,
            within_minutes=max(0, self.config.runtime.advisory_cooldown_minutes),
        ):
            message = (
                f"[跳过] {symbol}\n"
                f"原因: 冷却期内同方向建议已推送({self.config.runtime.advisory_cooldown_minutes}分钟)"
            )
            self.storage.insert_system_event(
                utc_now(),
                "advisory_cooldown_skip",
                {"symbol": symbol, "side": signal.side.value, "message": message},
            )
            return SymbolAnalysis(
                symbol=symbol,
                suitable=False,
                message=message,
                reasons=["advisory_cooldown_skip"],
                timeframe_mode=selected_mode,
            )
        self.storage.insert_advice_record(
            ts=snapshot.ts,
            advice_id=advisory.advice_id,
            symbol=symbol,
            side=signal.side.value,
            timeframe_mode=advisory.timeframe_mode,
            payload={"advisory": advisory},
        )
        message = advisory_to_telegram_text(advisory, snapshot)
        self.storage.insert_system_event(utc_now(), "trade_advisory_generated", {"symbol": symbol, "advisory": advisory})
        sent = False
        if push_to_telegram:
            ok, reason = self.notifier.send_text(message)
            sent = ok
            self.storage.insert_system_event(utc_now(), "telegram_advisory_sent", {"symbol": symbol, "sent": ok, "reason": reason})
        return SymbolAnalysis(
            symbol=symbol,
            suitable=True,
            message=message,
            reasons=["advisory_generated", "[telegram:sent]" if sent else "[telegram:not_sent]"],
            timeframe_mode=selected_mode,
        )

    def _refresh_account_for_symbol(self, symbol: str) -> None:
        _ = symbol
        # Advisory-only mode intentionally keeps exposure at zero.
        self.account.open_positions = 0
        self.account.open_risk_pct = 0.0
        self.account.symbol_notional_pct = 0.0

    def _record_config_version(self) -> None:
        payload = asdict(self.config)
        self.storage.insert_config_version(utc_now(), config_hash(payload), payload)

    def _pick_signal(
        self,
        snapshot: MarketSnapshot,
        timeframe_mode: TimeframeMode | str,
    ) -> tuple[str, SignalIntent | None, list[str]]:
        if timeframe_mode in {"15m", "1h", "hybrid"}:
            out = self.signal_engine.evaluate_explain(snapshot, timeframe_mode=timeframe_mode)
            return (timeframe_mode, out.signal, out.failed_reasons)

        # auto mode: conservative-first fallback chain.
        mode_chain: list[TimeframeMode] = ["1h", "hybrid", "15m"]
        failed: list[str] = []
        for mode in mode_chain:
            out = self.signal_engine.evaluate_explain(snapshot, timeframe_mode=mode)
            if out.signal is not None:
                return (mode, out.signal, [])
            failed.append(f"{mode}:{','.join(out.failed_reasons) if out.failed_reasons else 'signal_not_ready'}")
        return ("auto", None, failed)

    @staticmethod
    def _format_signal_reasons(codes: list[str]) -> str:
        if not codes:
            return "当前未同时满足趋势+回踩+突破+量能条件"
        translated = [SIGNAL_REASON_MAP.get(code, code) for code in codes]
        return "；".join(translated)

    @staticmethod
    def _format_signal_reasons_autoaware(codes: list[str]) -> str:
        if not codes:
            return TradingRuntime._format_signal_reasons(codes)
        if not any(":" in code for code in codes):
            return TradingRuntime._format_signal_reasons(codes)
        translated: list[str] = []
        for code in codes:
            if ":" not in code:
                translated.append(SIGNAL_REASON_MAP.get(code, code))
                continue
            tf, raw = code.split(":", maxsplit=1)
            parts = [p for p in raw.split(",") if p]
            human_parts = [SIGNAL_REASON_MAP.get(p, p) for p in parts]
            translated.append(f"{tf}=>{' + '.join(human_parts)}")
        return "；".join(translated)

    @staticmethod
    def _format_risk_reasons(codes: list[str]) -> str:
        if not codes:
            return "风险规则未通过"
        translated = [RISK_REASON_MAP.get(code, code) for code in codes]
        return "；".join(translated)

    @staticmethod
    def _atr_from_candles(candles: list[object], period: int = 14) -> float:
        if len(candles) < 2:
            return 0.0
        tr: list[float] = []
        prev_close = candles[0].close
        for c in candles[1:]:
            tr.append(max(c.high - c.low, abs(c.high - prev_close), abs(c.low - prev_close)))
            prev_close = c.close
        if len(tr) < period:
            period = len(tr)
        if period <= 0:
            return 0.0
        return sum(tr[-period:]) / float(period)
