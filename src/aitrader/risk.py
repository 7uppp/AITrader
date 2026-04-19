from __future__ import annotations

from dataclasses import dataclass

from .config import RiskConfig, TradingConfig
from .indicators import atr
from .types import AccountState, MarketSnapshot, RiskDecision, Side, SignalIntent, SystemMode


MAJOR_SYMBOLS = {"BTCUSDT", "ETHUSDT"}


@dataclass(slots=True)
class RiskEngine:
    trading: TradingConfig
    risk: RiskConfig

    def assess(self, signal: SignalIntent, snapshot: MarketSnapshot, account: AccountState, mode: SystemMode) -> RiskDecision:
        rejects: list[str] = []
        if mode == SystemMode.KILLED:
            return RiskDecision(False, ["system:killed"])
        if mode == SystemMode.PAUSED:
            return RiskDecision(False, ["system:paused"])
        if mode == SystemMode.RISK_OFF:
            return RiskDecision(False, ["system:risk_off"])
        if snapshot.is_stale:
            rejects.append("market:stale")
        if account.daily_pnl_pct <= -self.risk.daily_loss_limit_pct:
            rejects.append("risk:daily_limit_hit")
        if account.weekly_pnl_pct <= -self.risk.weekly_loss_limit_pct:
            rejects.append("risk:weekly_limit_hit")
        if account.drawdown_pct >= self.risk.max_drawdown_pct:
            rejects.append("risk:max_drawdown_hit")
        if account.consecutive_losses >= self.risk.max_consecutive_losses:
            rejects.append("risk:consecutive_losses_hit")
        if account.open_positions >= self.risk.max_open_positions:
            rejects.append("risk:max_open_positions_hit")
        if account.open_risk_pct >= self.risk.max_open_risk_pct:
            rejects.append("risk:max_open_risk_hit")
        if account.free_margin_pct < self.risk.min_free_margin_pct:
            rejects.append("risk:free_margin_low")
        if signal.risk_distance <= 0:
            rejects.append("risk:invalid_stop_distance")
        if abs(snapshot.funding_rate_pct) > self.risk.extreme_funding_abs_pct:
            rejects.append("risk:extreme_funding")
        if abs(snapshot.oi_change_1h_pct) > self.risk.max_oi_change_1h_pct:
            rejects.append("risk:oi_spike")
        if snapshot.bid_ask_spread_bps > 20.0:
            rejects.append("risk:spread_too_wide")
        if snapshot.estimated_slippage_bps > 30.0:
            rejects.append("risk:slippage_too_high")
        if self.trading.leverage > self.trading.max_leverage_hard:
            rejects.append("risk:leverage_above_hard_limit")

        liq_price = self._estimate_liquidation_price(signal.entry_price, signal.side)
        liq_buffer_pct = self._calc_liq_buffer_pct(signal.entry_price, liq_price, signal.side)
        if signal.symbol in MAJOR_SYMBOLS:
            min_buffer = self.risk.liquidation_buffer_pct_major
        else:
            min_buffer = self.risk.liquidation_buffer_pct_alt
        liq_distance = abs(signal.entry_price - liq_price)
        atr_1h = atr(snapshot.candles_1h[-30:], period=14)
        if liq_buffer_pct < min_buffer:
            rejects.append("risk:liq_buffer_too_low")
        if atr_1h > 0 and liq_distance < self.risk.min_liq_distance_atr_mult * atr_1h:
            rejects.append("risk:liq_distance_below_atr_rule")
        if liq_distance < self.risk.min_liq_stop_distance_ratio * signal.risk_distance:
            rejects.append("risk:liq_distance_below_stop_ratio")

        risk_amount = account.equity * (self.risk.single_trade_risk_pct / 100.0)
        qty_from_stop = 0.0 if signal.risk_distance <= 0 else risk_amount / signal.risk_distance
        qty_by_symbol_cap = (account.equity * (self.risk.max_symbol_notional_pct / 100.0)) / signal.entry_price
        qty = min(qty_from_stop, qty_by_symbol_cap)
        if qty <= 0:
            rejects.append("risk:position_size_zero")
        if account.symbol_notional_pct >= self.risk.max_symbol_notional_pct:
            rejects.append("risk:symbol_exposure_hit")
        if (account.open_risk_pct + self.risk.single_trade_risk_pct) > self.risk.max_open_risk_pct:
            rejects.append("risk:open_risk_after_trade_exceeds_limit")

        if rejects:
            return RiskDecision(
                approved=False,
                reason_codes=rejects,
                liquidation_price=liq_price,
                liquidation_buffer_pct=liq_buffer_pct,
            )
        return RiskDecision(
            approved=True,
            reason_codes=["risk:approved"],
            quantity=qty,
            liquidation_price=liq_price,
            liquidation_buffer_pct=liq_buffer_pct,
        )

    def _estimate_liquidation_price(self, entry: float, side: Side) -> float:
        # Conservative approximation to enforce a wide safety buffer before live exchange formulas are integrated.
        lev = max(self.trading.leverage, 1.0)
        mmr = self.risk.maintenance_margin_rate
        if side == Side.LONG:
            return entry * max(0.01, (1.0 - (1.0 / lev) + mmr))
        return entry * (1.0 + (1.0 / lev) - mmr)

    @staticmethod
    def _calc_liq_buffer_pct(entry: float, liq: float, side: Side) -> float:
        if entry <= 0:
            return 0.0
        if side == Side.LONG:
            return max(0.0, ((entry - liq) / entry) * 100.0)
        return max(0.0, ((liq - entry) / entry) * 100.0)
