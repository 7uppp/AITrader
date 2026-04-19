from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite

import httpx

from .config import BinanceConfig
from .types import Candle, MarketSnapshot


@dataclass(slots=True)
class BinanceMarketDataClient:
    config: BinanceConfig

    def server_time(self) -> datetime:
        payload = self._get("/fapi/v1/time")
        return datetime.fromtimestamp(payload["serverTime"] / 1000.0, tz=UTC)

    def exchange_info(self) -> dict[str, object]:
        return self._get("/fapi/v1/exchangeInfo")

    def fetch_snapshot(self, symbol: str) -> MarketSnapshot:
        candles_4h = self._fetch_klines(symbol, "4h", 220)
        candles_1h = self._fetch_klines(symbol, "1h", 120)
        candles_15m = self._fetch_klines(symbol, "15m", 80)
        premium = self._get("/fapi/v1/premiumIndex", params={"symbol": symbol})
        depth = self._get("/fapi/v1/depth", params={"symbol": symbol, "limit": 20})

        mark_price = float(premium["markPrice"])
        index_price = float(premium["indexPrice"])
        funding_rate_pct = float(premium["lastFundingRate"]) * 100.0
        oi_change_1h_pct = self._open_interest_change_1h(symbol)
        long_short_ratio = self._long_short_ratio(symbol)
        spread_bps = self._spread_bps(depth)
        slippage_bps = self._slippage_bps(depth, target_notional=10_000.0)
        atr_1h_percentile = self._atr_percentile(candles_1h)
        risk_extreme = abs(funding_rate_pct) >= 0.03 or abs(oi_change_1h_pct) >= 8.0

        return MarketSnapshot(
            symbol=symbol,
            ts=self.server_time(),
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

    def _fetch_klines(self, symbol: str, interval: str, limit: int) -> list[Candle]:
        rows = self._get("/fapi/v1/klines", params={"symbol": symbol, "interval": interval, "limit": limit})
        candles: list[Candle] = []
        for row in rows:
            candles.append(
                Candle(
                    ts=datetime.fromtimestamp(row[0] / 1000.0, tz=UTC),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                )
            )
        return candles

    def _open_interest_change_1h(self, symbol: str) -> float:
        try:
            rows = self._get("/futures/data/openInterestHist", params={"symbol": symbol, "period": "5m", "limit": 13})
            if len(rows) < 2:
                return 0.0
            first = self._read_float(rows[0], ("sumOpenInterest", "sumOpenInterestValue", "openInterest", "value"))
            last = self._read_float(rows[-1], ("sumOpenInterest", "sumOpenInterestValue", "openInterest", "value"))
            if first <= 0:
                return 0.0
            return ((last - first) / first) * 100.0
        except Exception:
            return 0.0

    def _long_short_ratio(self, symbol: str) -> float:
        try:
            rows = self._get("/futures/data/globalLongShortAccountRatio", params={"symbol": symbol, "period": "5m", "limit": 1})
            if not rows:
                return 1.0
            ratio = float(rows[-1].get("longShortRatio", 1.0))
            return ratio if isfinite(ratio) and ratio > 0 else 1.0
        except Exception:
            return 1.0

    @staticmethod
    def _spread_bps(depth: dict[str, object]) -> float:
        bids = depth.get("bids", [])
        asks = depth.get("asks", [])
        if not bids or not asks:
            return 999.0
        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
        mid = (best_bid + best_ask) / 2.0
        if mid <= 0:
            return 999.0
        return ((best_ask - best_bid) / mid) * 10_000.0

    @staticmethod
    def _slippage_bps(depth: dict[str, object], target_notional: float) -> float:
        bids = depth.get("bids", [])
        asks = depth.get("asks", [])
        if not bids or not asks:
            return 999.0
        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
        mid = (best_bid + best_ask) / 2.0
        if mid <= 0:
            return 999.0

        required_qty = target_notional / mid
        ask_vwap = BinanceMarketDataClient._vwap_from_book(asks, required_qty)
        bid_vwap = BinanceMarketDataClient._vwap_from_book(bids, required_qty)
        if ask_vwap <= 0 or bid_vwap <= 0:
            return 999.0
        slip_buy_bps = ((ask_vwap - mid) / mid) * 10_000.0
        slip_sell_bps = ((mid - bid_vwap) / mid) * 10_000.0
        return max(slip_buy_bps, slip_sell_bps)

    @staticmethod
    def _vwap_from_book(levels: list[list[object]], target_qty: float) -> float:
        remaining = target_qty
        cost = 0.0
        filled = 0.0
        for level in levels:
            price = float(level[0])
            qty = float(level[1])
            take = min(remaining, qty)
            cost += take * price
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

    def _get(self, path: str, params: dict[str, object] | None = None) -> object:
        url = f"{self.config.base_url}{path}"
        with httpx.Client(timeout=self.config.request_timeout_seconds) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            return response.json()

    @staticmethod
    def _read_float(row: dict[str, object], keys: tuple[str, ...]) -> float:
        for key in keys:
            if key in row:
                try:
                    return float(row[key])
                except Exception:
                    continue
        return 0.0
