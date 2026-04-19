from aitrader.runtime import TradingRuntime


def test_format_signal_reasons_human_readable():
    text = TradingRuntime._format_signal_reasons(["trend_not_confirmed", "pullback_not_confirmed"])
    assert "trend_not_confirmed" not in text
    assert "pullback_not_confirmed" not in text


def test_format_risk_reasons_human_readable():
    text = TradingRuntime._format_risk_reasons(["risk:extreme_funding"])
    assert "risk:extreme_funding" not in text


def test_format_signal_reasons_autoaware_contains_timeframe_prefix():
    text = TradingRuntime._format_signal_reasons_autoaware(["1h:trend_not_confirmed,pullback_not_confirmed"])
    assert "1h=>" in text
