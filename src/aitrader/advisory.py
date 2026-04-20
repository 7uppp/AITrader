from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from .config import AppConfig
from .types import MarketSnapshot, RiskDecision, Side, SignalIntent


@dataclass(slots=True)
class TradeAdvisory:
    advice_id: str
    symbol: str
    side: Side
    trigger_source: str
    entry_trigger: float
    entry_zone_low: float
    entry_zone_high: float
    stop_loss: float
    main_take_profit: float
    runner_activation_price: float
    runner_trailing_atr_mult: float
    suggested_quantity: float
    main_lot_ratio: float
    runner_lot_ratio: float
    main_quantity: float
    runner_quantity: float
    risk_distance: float
    confidence: float
    recommended_leverage: float
    recommended_leverage_reason: str
    timeframe_mode: str
    valid_minutes: int
    manual_total_usdt: float | None = None
    manual_main_usdt: float | None = None
    manual_runner_usdt: float | None = None
    manual_main_quantity: float | None = None
    manual_runner_quantity: float | None = None


def generate_advice_id(symbol: str, timeframe_mode: str, ts: datetime | None = None) -> str:
    now = ts or datetime.now(UTC)
    sym = symbol.upper().replace("USDT", "")
    tf = timeframe_mode.upper()
    suffix = uuid4().hex[:6].upper()
    return f"A-{sym}-{tf}-{now.strftime('%Y%m%d%H%M%S')}-{suffix}"


def build_trade_advisory(
    cfg: AppConfig,
    snapshot: MarketSnapshot,
    signal: SignalIntent,
    decision: RiskDecision,
    atr_15m: float,
    manual_total_usdt: float | None = None,
    advice_id: str | None = None,
) -> TradeAdvisory:
    one_r = signal.risk_distance
    zone_half = atr_15m * 0.10 if atr_15m > 0 else signal.entry_price * 0.001
    if signal.side == Side.LONG:
        main_tp = signal.entry_price + one_r
        runner_activation = signal.entry_price + cfg.strategy.runner_trailing_activation_r * one_r
    else:
        main_tp = signal.entry_price - one_r
        runner_activation = signal.entry_price - cfg.strategy.runner_trailing_activation_r * one_r
    trailing_mult = (
        cfg.strategy.runner_trailing_atr_mult_tight
        if snapshot.risk_extreme and cfg.strategy.risk_extreme_mode_tighten_trailing
        else cfg.strategy.runner_trailing_atr_mult
    )
    timeframe_mode = "hybrid"
    trigger_source = "待触发"
    for code in signal.reason_codes:
        if code.startswith("timeframe:"):
            timeframe_mode = code.split(":", maxsplit=1)[1]
        if code.startswith("trigger:"):
            trigger_source = code.split(":", maxsplit=1)[1]
    valid_minutes = 90 if timeframe_mode in {"1h", "1h_primary"} else (20 if timeframe_mode == "15m" else 45)
    main_usdt = None
    runner_usdt = None
    main_qty_by_usdt = None
    runner_qty_by_usdt = None
    if manual_total_usdt is not None and manual_total_usdt > 0 and signal.entry_price > 0:
        main_usdt = manual_total_usdt * cfg.strategy.main_lot_ratio
        runner_usdt = manual_total_usdt * cfg.strategy.runner_lot_ratio
        main_qty_by_usdt = main_usdt / signal.entry_price
        runner_qty_by_usdt = runner_usdt / signal.entry_price
    recommended_lev, lev_reason = _recommended_leverage(
        confidence=signal.confidence,
        timeframe_mode=timeframe_mode,
        risk_extreme=snapshot.risk_extreme,
        hard_limit=min(5.0, cfg.trading.max_leverage_hard),
    )
    resolved_advice_id = advice_id or generate_advice_id(signal.symbol, timeframe_mode, snapshot.ts)
    return TradeAdvisory(
        advice_id=resolved_advice_id,
        symbol=signal.symbol,
        side=signal.side,
        trigger_source=trigger_source,
        entry_trigger=signal.entry_price,
        entry_zone_low=signal.entry_price - zone_half,
        entry_zone_high=signal.entry_price + zone_half,
        stop_loss=signal.initial_stop,
        main_take_profit=main_tp,
        runner_activation_price=runner_activation,
        runner_trailing_atr_mult=trailing_mult,
        suggested_quantity=decision.quantity,
        main_lot_ratio=cfg.strategy.main_lot_ratio,
        runner_lot_ratio=cfg.strategy.runner_lot_ratio,
        main_quantity=decision.quantity * cfg.strategy.main_lot_ratio,
        runner_quantity=decision.quantity * cfg.strategy.runner_lot_ratio,
        risk_distance=one_r,
        confidence=signal.confidence,
        recommended_leverage=recommended_lev,
        recommended_leverage_reason=lev_reason,
        timeframe_mode=timeframe_mode,
        valid_minutes=valid_minutes,
        manual_total_usdt=manual_total_usdt,
        manual_main_usdt=main_usdt,
        manual_runner_usdt=runner_usdt,
        manual_main_quantity=main_qty_by_usdt,
        manual_runner_quantity=runner_qty_by_usdt,
    )


def advisory_to_telegram_text(ad: TradeAdvisory, snapshot: MarketSnapshot) -> str:
    side_cn = "做多" if ad.side == Side.LONG else "做空"
    timeframe_label = _human_timeframe(ad.timeframe_mode)
    trigger_label = _human_trigger(ad.trigger_source)
    extra_manual = ""
    if ad.manual_total_usdt is not None and ad.manual_total_usdt > 0:
        extra_manual = (
            f"你的总投入: {ad.manual_total_usdt:.2f} USDT\n"
            f"按主仓60%: {ad.manual_main_usdt:.2f} USDT -> 约 {ad.manual_main_quantity:.6f}\n"
            f"按副仓40%: {ad.manual_runner_usdt:.2f} USDT -> 约 {ad.manual_runner_quantity:.6f}\n"
        )

    return (
        f"[交易建议] {ad.symbol} {side_cn}\n"
        f"AdviceID: {ad.advice_id}\n"
        f"判定框架: {timeframe_label}\n"
        f"触发类型: {trigger_label}\n"
        f"建议有效期: 约{ad.valid_minutes}分钟\n"
        f"触发开仓价: {ad.entry_trigger:.4f}\n"
        f"建议开仓区间: {ad.entry_zone_low:.4f} - {ad.entry_zone_high:.4f}\n"
        f"止损价: {ad.stop_loss:.4f}\n"
        f"主仓止盈(+1R): {ad.main_take_profit:.4f}\n"
        f"副仓追踪激活价: {ad.runner_activation_price:.4f}\n"
        f"副仓追踪参数: ATR(15m) x {ad.runner_trailing_atr_mult:.2f}\n"
        f"建议总数量: {ad.suggested_quantity:.6f}\n"
        f"主仓建议({ad.main_lot_ratio * 100:.0f}%): {ad.main_quantity:.6f}\n"
        f"副仓建议({ad.runner_lot_ratio * 100:.0f}%): {ad.runner_quantity:.6f}\n"
        f"{extra_manual}"
        f"1R定义: 入场价与初始止损价的距离 = {ad.risk_distance:.4f}\n"
        "规则说明: 主仓在+1R止盈，副仓在+1.5R后启动追踪止损。\n"
        f"信号置信度: {ad.confidence:.2f}\n"
        f"建议杠杆: {ad.recommended_leverage:.1f}x ({ad.recommended_leverage_reason})\n"
        f"Mark/Funding/OI: {snapshot.mark_price:.4f} / {snapshot.funding_rate_pct:.4f}% / {snapshot.oi_change_1h_pct:.2f}%\n"
        f"平仓回报: /result {ad.advice_id} win 1.2"
    )


def _recommended_leverage(confidence: float, timeframe_mode: str, risk_extreme: bool, hard_limit: float) -> tuple[float, str]:
    if risk_extreme:
        return (1.0, "极端行情，强制降至1x")
    lev = 1.0
    if confidence >= 0.80:
        lev = 2.0
    elif confidence >= 0.70:
        lev = 1.5

    if timeframe_mode == "15m":
        lev = max(1.0, lev - 0.5)
    if timeframe_mode in {"hybrid", "1h", "1h_primary"}:
        lev = min(2.0, lev + 0.0)

    cap = max(1.0, hard_limit)
    lev = min(lev, cap)
    reason = f"基于置信度/周期，且不超过硬上限{cap:.1f}x"
    return (round(lev, 1), reason)


def _human_timeframe(timeframe_mode: str) -> str:
    if timeframe_mode in {"1h", "1h_primary", "hybrid"}:
        return "1H主导 / 15m触发"
    if timeframe_mode == "15m":
        return "15m快速模式"
    return timeframe_mode


def _human_trigger(trigger_source: str) -> str:
    mapping = {
        "bb_mid_reclaim": "布林中轨收复",
        "bb_mid_reject": "布林中轨压回",
        "structure_breakout": "结构突破",
        "structure_breakdown": "结构跌破",
    }
    return mapping.get(trigger_source, trigger_source)
