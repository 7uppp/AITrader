from aitrader.indicators import bollinger_bands, rolling_high, rolling_low, rsi, sma


def test_sma_returns_running_average():
    out = sma([1.0, 2.0, 3.0, 4.0], period=3)
    assert out == [1.0, 1.5, 2.0, 3.0]


def test_rsi_behaves_for_up_and_down_trends():
    up = rsi([float(v) for v in range(1, 21)], period=14)
    down = rsi([float(v) for v in range(20, 0, -1)], period=14)
    assert up[-1] > 70.0
    assert down[-1] < 30.0


def test_bollinger_bands_and_rolling_helpers_are_safe():
    values = [100.0, 101.0, 102.0, 103.0, 104.0]
    upper, middle, lower = bollinger_bands(values, period=20, stddev=2.0)
    assert len(upper) == len(values)
    assert len(middle) == len(values)
    assert len(lower) == len(values)
    assert upper[-1] > middle[-1] > lower[-1]

    highs = rolling_high(values, period=3)
    lows = rolling_low(values, period=3)
    assert highs[-1] == 104.0
    assert lows[-1] == 102.0
