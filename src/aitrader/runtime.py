from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any

import httpx

from .advisory import TradeAdvisory, advisory_to_telegram_text, build_trade_advisory
from .audit import config_hash
from .binance_market_data import BinanceMarketDataClient
from .execution import ExecutionEngine, PaperExecutionAdapter
from .config import AppConfig
from .hyperliquid_live import HyperliquidLiveAdapter
from .hyperliquid_market_data import HyperliquidMarketDataClient
from .market_data import MarketDataPolicy, MarketDataValidator
from .position_manager import PositionManager
from .risk import RiskEngine
from .storage import ActiveAdvice, Storage
from .strategy import SignalEngine, TimeframeMode
from .telegram_notify import TelegramNotifier
from .time_utils import utc_now
from .types import AccountState, MarketSnapshot, Side, SignalIntent, SystemMode


SIGNAL_REASON_MAP: dict[str, str] = {
    "market_stale": "行情数据过期",
    "insufficient_history": "K线历史长度不足",
    "trend_not_confirmed": "1H趋势未确认",
    "long_disabled": "当前配置禁用做多",
    "short_disabled": "当前配置禁用做空",
    "setup_not_confirmed": "1H回踩结构未就绪",
    "trigger_not_confirmed": "15m触发条件未就绪",
    "rsi_out_of_range": "RSI不在可交易区间",
    "confidence_below_threshold": "综合置信度不足",
    "atr_invalid": "ATR计算无效",
    "risk_distance_invalid": "止损距离无效",
    "trend:1h_long": "1H趋势做多",
    "trend:1h_short": "1H趋势做空",
    "setup:pullback": "回踩成立",
    "trigger:bb_mid_reclaim": "布林中轨收复",
    "trigger:bb_mid_reject": "布林中轨压回",
    "trigger:structure_breakout": "结构突破",
    "trigger:structure_breakdown": "结构跌破",
    "confirm:rsi_ok": "RSI确认通过",
    "confirm:bb_ok": "布林位置确认通过",
    "confirm:volume_ok": "量能确认通过",
    "confirm:4h_bias_aligned": "4H顺风同向",
    "confirm:volatility_low": "波动偏低，信号降权",
    "confirm:volatility_high": "波动偏高，信号降权",
    "confirm:risk_extreme": "资金费率/OI过热，信号降权",
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
    timeframe_mode: str = "1h_primary"


@dataclass(slots=True)
class AutoTradeCandidate:
    symbol: str
    advice_id: str
    advisory: TradeAdvisory
    score: float


@dataclass(slots=True)
class TradingRuntime:
    config: AppConfig
    data_client: Any
    signal_engine: SignalEngine
    risk_engine: RiskEngine
    notifier: TelegramNotifier
    market_validator: MarketDataValidator
    storage: Storage
    mode: SystemMode
    account: AccountState
    position_manager: PositionManager
    execution_engine: ExecutionEngine | None = None

    @classmethod
    def from_config(cls, cfg: AppConfig) -> "TradingRuntime":
        storage = Storage(Path(cfg.runtime.database_path))
        storage.path.parent.mkdir(parents=True, exist_ok=True)
        storage.init_schema()
        exchange_kind = cfg.exchange.kind.strip().lower()
        if exchange_kind == "hyperliquid":
            data_client: Any = HyperliquidMarketDataClient(cfg.hyperliquid)
        else:
            data_client = BinanceMarketDataClient(cfg.binance)

        execution_engine: ExecutionEngine | None = None
        if cfg.runtime.auto_trade_enabled:
            if exchange_kind == "hyperliquid":
                execution_engine = ExecutionEngine(
                    adapter=HyperliquidLiveAdapter(
                        config=cfg.hyperliquid,
                        dry_run=cfg.runtime.dry_run,
                    )
                )
            else:
                execution_engine = ExecutionEngine(adapter=PaperExecutionAdapter())

        equity = max(100.0, float(cfg.runtime.assumed_equity_usd))
        runtime = cls(
            config=cfg,
            data_client=data_client,
            signal_engine=SignalEngine(cfg.trading, cfg.strategy),
            risk_engine=RiskEngine(cfg.trading, cfg.risk),
            notifier=TelegramNotifier(cfg.telegram),
            market_validator=MarketDataValidator(MarketDataPolicy()),
            storage=storage,
            mode=cfg.system.mode,
            account=AccountState(
                equity=equity,
                free_margin=equity,
                daily_pnl_pct=0.0,
                weekly_pnl_pct=0.0,
                drawdown_pct=0.0,
                consecutive_losses=0,
                open_positions=0,
                open_risk_pct=0.0,
                symbol_notional_pct=0.0,
            ),
            position_manager=PositionManager(cfg.strategy, cfg.risk),
            execution_engine=execution_engine,
        )
        runtime._record_config_version()
        if cfg.runtime.auto_trade_enabled and cfg.runtime.advisory_only:
            runtime.storage.insert_system_event(
                utc_now(),
                "runtime_auto_trade_disabled",
                {"message": "auto_trade_enabled=true but advisory_only=true, skip live execution"},
            )
        return runtime

    def run_cycle(self) -> RuntimeResult:
        if self.config.runtime.auto_trade_enabled and not self.config.runtime.advisory_only:
            return self._run_auto_trade_cycle()
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

    def _run_auto_trade_cycle(self) -> RuntimeResult:
        analyses = self.analyze_symbols(self.config.trading.symbols, push_to_telegram=False, timeframe_mode="auto")
        result = RuntimeResult(processed_symbols=len(analyses))
        candidates = self._collect_auto_candidates(analyses)
        result.signals = len(candidates)
        selected = self._select_auto_candidates(candidates)
        skipped = max(0, len(candidates) - len(selected))
        if skipped > 0:
            self.storage.insert_system_event(
                utc_now(),
                "auto_trade_candidates_skipped",
                {"candidate_count": len(candidates), "selected_count": len(selected)},
            )

        for candidate in selected:
            ok = self._execute_candidate(candidate)
            if ok:
                result.approved += 1
                result.advisories_generated += 1
                result.advisories_sent += 1
            else:
                result.rejected += 1

        result.rejected += sum(1 for a in analyses if not a.suitable)
        return result

    def _collect_auto_candidates(self, analyses: list[SymbolAnalysis]) -> list[AutoTradeCandidate]:
        candidates: list[AutoTradeCandidate] = []
        for analysis in analyses:
            if not analysis.suitable:
                continue
            advice_id = self._extract_reason_value(analysis.reasons, "advice_id")
            if not advice_id:
                continue
            if "active_advice_exists" in analysis.reasons:
                continue
            row = self.storage.get_advice_record(advice_id)
            if row is None:
                continue
            payload = self._safe_load_json(str(row["payload_json"]))
            advisory_raw = payload.get("advisory")
            if not isinstance(advisory_raw, dict):
                continue
            advisory = self._advisory_from_payload(advisory_raw)
            score = self._candidate_score(advisory)
            candidates.append(
                AutoTradeCandidate(
                    symbol=analysis.symbol,
                    advice_id=advice_id,
                    advisory=advisory,
                    score=score,
                )
            )
        return candidates

    def _select_auto_candidates(self, candidates: list[AutoTradeCandidate]) -> list[AutoTradeCandidate]:
        if not candidates:
            return []
        self._refresh_account_for_symbol(symbol="")
        max_positions = max(0, self.config.risk.max_open_positions - self.account.open_positions)
        max_candidates = max(1, self.config.runtime.max_candidates_per_cycle)
        remaining_slots = min(max_positions, max_candidates)
        if remaining_slots <= 0:
            return []

        risk_left = max(0.0, self.config.risk.max_open_risk_pct - self.account.open_risk_pct)
        picked: list[AutoTradeCandidate] = []
        seen_symbols: set[str] = set()
        sorted_candidates = sorted(candidates, key=lambda c: c.score, reverse=True)
        for c in sorted_candidates:
            if remaining_slots <= 0 or risk_left <= 0:
                break
            if c.symbol in seen_symbols:
                continue
            if self._has_active_lots(c.symbol):
                continue
            scaled_qty = self._scale_quantity_by_budget(c.advisory.suggested_quantity, risk_left)
            if scaled_qty <= 0:
                continue
            c.advisory.suggested_quantity = scaled_qty
            c.advisory.main_quantity = scaled_qty * self.config.strategy.main_lot_ratio
            c.advisory.runner_quantity = scaled_qty * self.config.strategy.runner_lot_ratio
            picked.append(c)
            seen_symbols.add(c.symbol)
            remaining_slots -= 1
            risk_left -= min(self.config.risk.single_trade_risk_pct, risk_left)
        return picked

    def _execute_candidate(self, candidate: AutoTradeCandidate) -> bool:
        if self.execution_engine is None:
            self.storage.insert_system_event(
                utc_now(),
                "auto_trade_no_execution_engine",
                {"advice_id": candidate.advice_id, "symbol": candidate.symbol},
            )
            return False

        adapter = self.execution_engine.adapter
        if not self.config.runtime.dry_run and not hasattr(adapter, "submit_protection_orders"):
            self.storage.insert_system_event(
                utc_now(),
                "auto_trade_missing_protection_support",
                {"advice_id": candidate.advice_id, "symbol": candidate.symbol},
            )
            return False

        side = candidate.advisory.side
        symbol = candidate.symbol
        main_qty = max(0.0, candidate.advisory.main_quantity)
        runner_qty = max(0.0, candidate.advisory.runner_quantity)
        if main_qty <= 0 and runner_qty <= 0:
            return False

        try:
            entry_price = candidate.advisory.entry_trigger
            if main_qty > 0:
                main_order = self.execution_engine.place(
                    request_id=f"{candidate.advice_id}-main",
                    symbol=symbol,
                    side=side,
                    quantity=main_qty,
                    price=entry_price,
                    reduce_only=False,
                )
                if main_order is not None:
                    self.storage.insert_order(utc_now(), main_order.client_order_id, symbol, side.value, main_order.status, main_order)
            if runner_qty > 0:
                runner_order = self.execution_engine.place(
                    request_id=f"{candidate.advice_id}-runner",
                    symbol=symbol,
                    side=side,
                    quantity=runner_qty,
                    price=entry_price,
                    reduce_only=False,
                )
                if runner_order is not None:
                    self.storage.insert_order(utc_now(), runner_order.client_order_id, symbol, side.value, runner_order.status, runner_order)

            total_qty = main_qty + runner_qty
            if total_qty > 0 and hasattr(adapter, "submit_protection_orders"):
                adapter.submit_protection_orders(
                    symbol=symbol,
                    side=side,
                    quantity=total_qty,
                    stop_price=candidate.advisory.stop_loss,
                    main_tp_price=candidate.advisory.main_take_profit,
                )

            signal = SignalIntent(
                symbol=symbol,
                side=side,
                entry_price=entry_price,
                initial_stop=candidate.advisory.stop_loss,
                confidence=candidate.advisory.confidence,
                reason_codes=[f"advice_id:{candidate.advice_id}"],
            )
            self.position_manager.open_split_position(signal=signal, quantity=total_qty)
            self._refresh_account_for_symbol(symbol=symbol)

            msg = (
                "[AUTO EXECUTED]\n"
                f"exchange={self.config.exchange.kind}\n"
                f"score={candidate.score:.3f}\n"
                f"symbol={symbol} side={side.value}\n"
                f"entry={entry_price:.6f} stop={candidate.advisory.stop_loss:.6f} tp_main={candidate.advisory.main_take_profit:.6f}\n"
                f"qty_total={total_qty:.6f} main={main_qty:.6f} runner={runner_qty:.6f}\n"
                f"advice_id={candidate.advice_id}"
            )
            sent, reason = self.notifier.send_text(msg[:3800])
            self.storage.insert_system_event(
                utc_now(),
                "auto_trade_executed",
                {
                    "advice_id": candidate.advice_id,
                    "symbol": symbol,
                    "score": candidate.score,
                    "main_qty": main_qty,
                    "runner_qty": runner_qty,
                    "telegram_sent": sent,
                    "telegram_reason": reason,
                },
            )
            return True
        except Exception as exc:
            self.storage.insert_system_event(
                utc_now(),
                "auto_trade_execute_error",
                {"advice_id": candidate.advice_id, "symbol": symbol, "error": type(exc).__name__, "message": str(exc)},
            )
            return False

    def _scale_quantity_by_budget(self, quantity: float, risk_left_pct: float) -> float:
        if quantity <= 0:
            return 0.0
        base_risk = max(0.0001, self.config.risk.single_trade_risk_pct)
        scale = min(1.0, max(0.0, risk_left_pct / base_risk))
        return quantity * scale

    def _has_active_lots(self, symbol: str) -> bool:
        for lot in self.position_manager.lots:
            if lot.active and lot.symbol.upper() == symbol.upper():
                return True
        return False

    @staticmethod
    def _extract_reason_value(reasons: list[str], key: str) -> str | None:
        prefix = f"{key}:"
        for reason in reasons:
            if reason.startswith(prefix):
                return reason[len(prefix) :].strip() or None
        return None

    @staticmethod
    def _candidate_score(advisory: TradeAdvisory) -> float:
        score = advisory.confidence
        if advisory.timeframe_mode in {"1h_primary", "1h", "hybrid"}:
            score += 0.04
        if advisory.recommended_leverage <= 1.0:
            score -= 0.03
        return round(score, 4)

    @staticmethod
    def _safe_load_json(raw: str) -> dict[str, object]:
        try:
            out = json.loads(raw)
            return out if isinstance(out, dict) else {}
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _advisory_from_payload(payload: dict[str, object]) -> TradeAdvisory:
        side = payload.get("side", "LONG")
        side_value = side if isinstance(side, str) else str(side)
        if "LONG" in side_value.upper():
            side_enum = Side.LONG
        elif "SHORT" in side_value.upper():
            side_enum = Side.SHORT
        else:
            side_enum = Side.LONG
        return TradeAdvisory(
            advice_id=str(payload.get("advice_id", "")),
            symbol=str(payload.get("symbol", "")),
            side=side_enum,
            trigger_source=str(payload.get("trigger_source", "pending")),
            entry_trigger=float(payload.get("entry_trigger", 0.0)),
            entry_zone_low=float(payload.get("entry_zone_low", 0.0)),
            entry_zone_high=float(payload.get("entry_zone_high", 0.0)),
            stop_loss=float(payload.get("stop_loss", 0.0)),
            main_take_profit=float(payload.get("main_take_profit", 0.0)),
            runner_activation_price=float(payload.get("runner_activation_price", 0.0)),
            runner_trailing_atr_mult=float(payload.get("runner_trailing_atr_mult", 0.0)),
            suggested_quantity=float(payload.get("suggested_quantity", 0.0)),
            main_lot_ratio=float(payload.get("main_lot_ratio", 0.6)),
            runner_lot_ratio=float(payload.get("runner_lot_ratio", 0.4)),
            main_quantity=float(payload.get("main_quantity", 0.0)),
            runner_quantity=float(payload.get("runner_quantity", 0.0)),
            risk_distance=float(payload.get("risk_distance", 0.0)),
            confidence=float(payload.get("confidence", 0.0)),
            recommended_leverage=float(payload.get("recommended_leverage", 1.0)),
            recommended_leverage_reason=str(payload.get("recommended_leverage_reason", "")),
            timeframe_mode=str(payload.get("timeframe_mode", "auto")),
            valid_minutes=int(payload.get("valid_minutes", 30)),
            manual_total_usdt=float(payload["manual_total_usdt"]) if payload.get("manual_total_usdt") is not None else None,
            manual_main_usdt=float(payload["manual_main_usdt"]) if payload.get("manual_main_usdt") is not None else None,
            manual_runner_usdt=float(payload["manual_runner_usdt"]) if payload.get("manual_runner_usdt") is not None else None,
            manual_main_quantity=float(payload["manual_main_quantity"]) if payload.get("manual_main_quantity") is not None else None,
            manual_runner_quantity=float(payload["manual_runner_quantity"]) if payload.get("manual_runner_quantity") is not None else None,
        )

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
                if push_to_telegram and self.config.telegram.send_rejections:
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
            if push_to_telegram and self.config.telegram.send_rejections:
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
        active_advice = self.storage.get_active_advice(symbol=symbol, side=signal.side.value, now=utc_now())
        if active_advice is not None:
            if push_to_telegram:
                message = self._format_active_skip_message(symbol=symbol, side=signal.side.value, active=active_advice)
                self.storage.insert_system_event(
                    utc_now(),
                    "active_advice_skip",
                    {
                        "symbol": symbol,
                        "side": signal.side.value,
                        "active_advice_id": active_advice.advice_id,
                        "remaining_minutes": active_advice.remaining_minutes,
                    },
                )
                return SymbolAnalysis(
                    symbol=symbol,
                    suitable=False,
                    message=message,
                    reasons=["active_advice_exists"],
                    timeframe_mode=selected_mode,
                )
            message = self._format_manual_reference_message(
                symbol=symbol,
                side=signal.side.value,
                advisory=advisory,
                snapshot=snapshot,
                mode=selected_mode,
                active=active_advice,
            )
            return SymbolAnalysis(
                symbol=symbol,
                suitable=True,
                message=message,
                reasons=["active_advice_exists", "manual_reference_only"],
                timeframe_mode=selected_mode,
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
            reasons=[
                "advisory_generated",
                f"advice_id:{advisory.advice_id}",
                "[telegram:sent]" if sent else "[telegram:not_sent]",
            ],
            timeframe_mode=selected_mode,
        )

    def _refresh_account_for_symbol(self, symbol: str) -> None:
        active_lots = [lot for lot in self.position_manager.lots if lot.active]
        unique_positions = {(lot.symbol.upper(), lot.side.value) for lot in active_lots}
        self.account.open_positions = len(unique_positions)
        self.account.open_risk_pct = min(
            self.config.risk.max_open_risk_pct,
            float(self.account.open_positions) * self.config.risk.single_trade_risk_pct,
        )
        if not symbol:
            self.account.symbol_notional_pct = 0.0
            return
        symbol_upper = symbol.upper()
        symbol_notional = 0.0
        for lot in active_lots:
            if lot.symbol.upper() == symbol_upper:
                symbol_notional += lot.quantity * lot.avg_entry
        if self.account.equity > 0:
            self.account.symbol_notional_pct = (symbol_notional / self.account.equity) * 100.0
        else:
            self.account.symbol_notional_pct = 0.0

    def _record_config_version(self) -> None:
        payload = asdict(self.config)
        self.storage.insert_config_version(utc_now(), config_hash(payload), payload)

    def _pick_signal(
        self,
        snapshot: MarketSnapshot,
        timeframe_mode: TimeframeMode | str,
    ) -> tuple[str, SignalIntent | None, list[str]]:
        if timeframe_mode in {"15m", "1h", "hybrid", "1h_primary"}:
            out = self.signal_engine.evaluate_explain(snapshot, timeframe_mode=timeframe_mode)
            resolved_mode = "1h_primary" if timeframe_mode in {"1h", "hybrid"} else str(timeframe_mode)
            if out.signal is not None:
                for code in out.signal.reason_codes:
                    if code.startswith("timeframe:"):
                        resolved_mode = code.split(":", maxsplit=1)[1]
                        break
            return (resolved_mode, out.signal, out.failed_reasons)

        mode_chain: list[TimeframeMode] = ["1h_primary", "15m"]
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
            return "当前未同时满足趋势、回踩与15m触发条件"
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

    @staticmethod
    def _format_active_skip_message(symbol: str, side: str, active: ActiveAdvice) -> str:
        side_cn = "做多" if side.upper() == "LONG" else "做空"
        entry_text = f"{active.entry_trigger:.4f}" if active.entry_trigger is not None else "-"
        return (
            f"[跳过] {symbol}\n"
            f"原因: 已有未结束的{side_cn}建议({active.advice_id})\n"
            f"原建议开仓价: {entry_text}\n"
            f"剩余有效期: 约{active.remaining_minutes}分钟\n"
            "说明: 等待 /result 回报或建议过期后，再接收新的同向推送。"
        )

    @staticmethod
    def _format_manual_reference_message(
        symbol: str,
        side: str,
        advisory: TradeAdvisory,
        snapshot: MarketSnapshot,
        mode: str,
        active: ActiveAdvice,
    ) -> str:
        side_cn = "做多" if side.upper() == "LONG" else "做空"
        active_entry = f"{active.entry_trigger:.4f}" if active.entry_trigger is not None else "-"
        return (
            f"[参考] {symbol} {side_cn}\n"
            f"判定框架: {mode}\n"
            f"当前分析开仓触发价: {advisory.entry_trigger:.4f}\n"
            f"当前分析止损价: {advisory.stop_loss:.4f}\n"
            f"当前分析主仓止盈: {advisory.main_take_profit:.4f}\n"
            f"Mark/Funding/OI: {snapshot.mark_price:.4f} / {snapshot.funding_rate_pct:.4f}% / {snapshot.oi_change_1h_pct:.2f}%\n"
            f"已有活动建议: {active.advice_id} (开仓价{active_entry}, 剩余约{active.remaining_minutes}分钟)\n"
            "说明: 本次为手动查询参考，不会生成新的同向推送建议。"
        )
