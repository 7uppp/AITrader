from __future__ import annotations

from math import sqrt

from .types import Candle


def sma(values: list[float], period: int) -> list[float]:
    if period <= 0:
        raise ValueError("period must be positive")
    if not values:
        return []
    out: list[float] = []
    running = 0.0
    for idx, value in enumerate(values):
        running += value
        if idx >= period:
            running -= values[idx - period]
        window = min(idx + 1, period)
        out.append(running / float(window))
    return out


def ema(values: list[float], period: int) -> list[float]:
    if period <= 0:
        raise ValueError("period must be positive")
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    out = [values[0]]
    for v in values[1:]:
        out.append(alpha * v + (1.0 - alpha) * out[-1])
    return out


def atr(candles: list[Candle], period: int = 14) -> float:
    if len(candles) < 2:
        return 0.0
    true_ranges: list[float] = []
    prev_close = candles[0].close
    for c in candles[1:]:
        tr = max(c.high - c.low, abs(c.high - prev_close), abs(c.low - prev_close))
        true_ranges.append(tr)
        prev_close = c.close
    if not true_ranges:
        return 0.0
    period = min(period, len(true_ranges))
    return sum(true_ranges[-period:]) / float(period)


def rsi(values: list[float], period: int = 14) -> list[float]:
    if period <= 0:
        raise ValueError("period must be positive")
    if not values:
        return []
    if len(values) == 1:
        return [50.0]

    gains: list[float] = [0.0]
    losses: list[float] = [0.0]
    for idx in range(1, len(values)):
        delta = values[idx] - values[idx - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))

    avg_gains = sma(gains, period)
    avg_losses = sma(losses, period)
    out: list[float] = []
    for gain, loss in zip(avg_gains, avg_losses, strict=False):
        if loss == 0 and gain == 0:
            out.append(50.0)
            continue
        if loss == 0:
            out.append(100.0)
            continue
        rs = gain / loss
        out.append(100.0 - (100.0 / (1.0 + rs)))
    return out


def bollinger_bands(values: list[float], period: int = 20, stddev: float = 2.0) -> tuple[list[float], list[float], list[float]]:
    if period <= 0:
        raise ValueError("period must be positive")
    if stddev <= 0:
        raise ValueError("stddev must be positive")
    if not values:
        return ([], [], [])

    middle = sma(values, period)
    upper: list[float] = []
    lower: list[float] = []
    for idx, value in enumerate(values):
        window_start = max(0, idx - period + 1)
        window = values[window_start : idx + 1]
        mean = middle[idx]
        variance = sum((item - mean) ** 2 for item in window) / float(len(window))
        deviation = sqrt(variance)
        upper.append(mean + stddev * deviation)
        lower.append(mean - stddev * deviation)
    return (upper, middle, lower)


def rolling_high(values: list[float], period: int) -> list[float]:
    if period <= 0:
        raise ValueError("period must be positive")
    if not values:
        return []
    out: list[float] = []
    for idx in range(len(values)):
        window = values[max(0, idx - period + 1) : idx + 1]
        out.append(max(window))
    return out


def rolling_low(values: list[float], period: int) -> list[float]:
    if period <= 0:
        raise ValueError("period must be positive")
    if not values:
        return []
    out: list[float] = []
    for idx in range(len(values)):
        window = values[max(0, idx - period + 1) : idx + 1]
        out.append(min(window))
    return out
