from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from .config import HyperliquidConfig
from .types import Candle, MarketSnapshot


def _to_hl_coin(symbol: str) -> str:
    token = symbol.upper().strip()
    if token.endswith("USDT"):
        return token[:-4]
    return token


@dataclass(slots=True)
class HyperliquidMarketDataClient:
    config: HyperliquidConfig

    def server_time(self) -> datetime:
        return datetime.now(UTC)

    def fetch_snapshot(self, symbol: str) -> MarketSnapshot:
        coin = _to_hl_coin(symbol)
        now = self.server_time()
        now_ms = int(now.timestamp() * 1000)
        candles_4h = self._fetch_candles(coin, "4h", 220, end_ms=now_ms)
        candles_1h = self._fetch_candles(coin, "1h", 120, end_ms=now_ms)
        candles_15m = self._fetch_candles(coin, "15m", 80, end_ms=now_ms)

        mids = self._all_mids()
        mark_price = self._safe_float(mids.get(coin), candles_15m[-1].close if candles_15m else 0.0)
        index_price = mark_price

        asset_ctx = self._asset_context(coin)
        funding_rate_pct = self._safe_float(asset_ctx.get("funding"), 0.0) * 100.0
        oi_change_1h_pct = 0.0
        long_short_ratio = 1.0

        depth = self._l2_book(coin)
        spread_bps = self._spread_bps(depth)
        slippage_bps = self._slippage_bps(depth, target_notional=10_000.0)
        atr_1h_percentile = self._atr_percentile(candles_1h)
        risk_extreme = abs(funding_rate_pct) >= 0.05 or abs(oi_change_1h_pct) >= 8.0

        return MarketSnapshot(
            symbol=symbol.upper(),
            ts=now,
            candles_4h=candles_4h,
            candles_1h=candles_1h,
            candles_15m=candles_15m,
            mark_price=mark_price,
            index_price=index_price,
            funding_rate_pct=funding_rate_pct,
            oi_change_1h_pct=oi_change_1h_pct,
            long_short_ratio=long_short_ratio,
            bid_ask_spread_bps=spread_bps,
            estimated_slippage_bps=slippage_bps,
            atr_1h_percentile=atr_1h_percentile,
            is_stale=False,
            risk_extreme=risk_extreme,
        )

    def _fetch_candles(self, coin: str, interval: str, limit: int, end_ms: int) -> list[Candle]:
        minutes = self._interval_to_minutes(interval)
        window_ms = max(limit, 2) * minutes * 60_000
        start_ms = end_ms - window_ms
        payload = {
            "type": "candleSnapshot",
            "req": {"coin": coin, "interval": interval, "startTime": start_ms, "endTime": end_ms},
        }
        raw_rows = self._post_info(payload)
        if isinstance(raw_rows, dict) and isinstance(raw_rows.get("candles"), list):
            rows = raw_rows.get("candles", [])
        elif isinstance(raw_rows, list):
            rows = raw_rows
        else:
            rows = []
        candles: list[Candle] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            ts_ms = int(self._safe_float(row.get("t"), 0.0))
            candles.append(
                Candle(
                    ts=datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC),
                    open=self._safe_float(row.get("o"), 0.0),
                    high=self._safe_float(row.get("h"), 0.0),
                    low=self._safe_float(row.get("l"), 0.0),
                    close=self._safe_float(row.get("c"), 0.0),
                    volume=self._safe_float(row.get("v"), 0.0),
                )
            )
        if len(candles) > limit:
            candles = candles[-limit:]
        return candles

    def _all_mids(self) -> dict[str, object]:
        payload = {"type": "allMids"}
        out = self._post_info(payload)
        return out if isinstance(out, dict) else {}

    def _asset_context(self, coin: str) -> dict[str, object]:
        payload = {"type": "metaAndAssetCtxs"}
        out = self._post_info(payload)
        if isinstance(out, list) and len(out) >= 2:
            meta = out[0]
            contexts = out[1]
        elif isinstance(out, dict):
            meta = out.get("meta", {})
            contexts = out.get("assetCtxs", [])
        else:
            return {}
        if not isinstance(meta, dict) or not isinstance(contexts, list):
            return {}
        universe = meta.get("universe", [])
        if not isinstance(universe, list):
            return {}
        for idx, item in enumerate(universe):
            if not isinstance(item, dict):
                continue
            if str(item.get("name", "")).upper() == coin.upper() and idx < len(contexts):
                ctx = contexts[idx]
                return ctx if isinstance(ctx, dict) else {}
        return {}

    def _l2_book(self, coin: str) -> dict[str, object]:
        out: object
        try:
            out = self._post_info({"type": "l2Book", "coin": coin})
        except Exception:
            out = self._post_info({"type": "l2Book", "req": {"coin": coin}})
        return out if isinstance(out, dict) else {}

    @staticmethod
    def _interval_to_minutes(interval: str) -> int:
        mapping = {"15m": 15, "1h": 60, "4h": 240}
        return mapping.get(interval, 60)

    @staticmethod
    def _best_prices(depth: dict[str, object]) -> tuple[float, float]:
        levels = depth.get("levels", [])
        if not isinstance(levels, list) or len(levels) < 2:
            return (0.0, 0.0)
        bids = levels[0]
        asks = levels[1]
        if not isinstance(bids, list) or not isinstance(asks, list) or not bids or not asks:
            return (0.0, 0.0)
        best_bid = HyperliquidMarketDataClient._safe_float((bids[0] or {}).get("px"), 0.0)
        best_ask = HyperliquidMarketDataClient._safe_float((asks[0] or {}).get("px"), 0.0)
        return (best_bid, best_ask)

    @staticmethod
    def _spread_bps(depth: dict[str, object]) -> float:
        best_bid, best_ask = HyperliquidMarketDataClient._best_prices(depth)
        if best_bid <= 0 or best_ask <= 0:
            return 999.0
        mid = (best_bid + best_ask) / 2.0
        if mid <= 0:
            return 999.0
        return ((best_ask - best_bid) / mid) * 10_000.0

    @staticmethod
    def _slippage_bps(depth: dict[str, object], target_notional: float) -> float:
        levels = depth.get("levels", [])
        if not isinstance(levels, list) or len(levels) < 2:
            return 999.0
        bids = levels[0] if isinstance(levels[0], list) else []
        asks = levels[1] if isinstance(levels[1], list) else []
        best_bid, best_ask = HyperliquidMarketDataClient._best_prices(depth)
        if best_bid <= 0 or best_ask <= 0:
            return 999.0
        mid = (best_bid + best_ask) / 2.0
        required_qty = target_notional / mid if mid > 0 else 0.0
        ask_vwap = HyperliquidMarketDataClient._vwap(asks, required_qty)
        bid_vwap = HyperliquidMarketDataClient._vwap(bids, required_qty)
        if ask_vwap <= 0 or bid_vwap <= 0:
            return 999.0
        slip_buy_bps = ((ask_vwap - mid) / mid) * 10_000.0
        slip_sell_bps = ((mid - bid_vwap) / mid) * 10_000.0
        return max(slip_buy_bps, slip_sell_bps)

    @staticmethod
    def _vwap(levels: list[object], target_qty: float) -> float:
        if target_qty <= 0:
            return 0.0
        remaining = target_qty
        cost = 0.0
        filled = 0.0
        for raw in levels:
            if not isinstance(raw, dict):
                continue
            px = HyperliquidMarketDataClient._safe_float(raw.get("px"), 0.0)
            sz = HyperliquidMarketDataClient._safe_float(raw.get("sz"), 0.0)
            if px <= 0 or sz <= 0:
                continue
            take = min(remaining, sz)
            cost += take * px
            filled += take
            remaining -= take
            if remaining <= 0:
                break
        if filled <= 0:
            return 0.0
        return cost / filled

    @staticmethod
    def _atr_percentile(candles_1h: list[Candle]) -> float:
        if len(candles_1h) < 30:
            return 0.5
        tr: list[float] = []
        prev = candles_1h[0].close
        for c in candles_1h[1:]:
            tr.append(max(c.high - c.low, abs(c.high - prev), abs(c.low - prev)))
            prev = c.close
        if len(tr) < 14:
            return 0.5
        atr_series: list[float] = []
        for i in range(14, len(tr) + 1):
            atr_series.append(sum(tr[i - 14 : i]) / 14.0)
        if not atr_series:
            return 0.5
        current = atr_series[-1]
        rank = sum(1 for v in atr_series if v <= current)
        return max(0.0, min(1.0, rank / float(len(atr_series))))

    def _post_info(self, payload: dict[str, object]) -> object:
        with httpx.Client(timeout=self.config.request_timeout_seconds) as client:
            response = client.post(f"{self.config.api_url}/info", json=payload)
            response.raise_for_status()
            return response.json()

    @staticmethod
    def _safe_float(raw: object, default: float) -> float:
        try:
            return float(raw)
        except (TypeError, ValueError):
            return default
