from aitrader.runtime import TradingRuntime


def test_format_signal_reasons_human_readable():
    text = TradingRuntime._format_signal_reasons(["trend_not_confirmed", "setup_not_confirmed"])
    assert "trend_not_confirmed" not in text
    assert "setup_not_confirmed" not in text


def test_format_risk_reasons_human_readable():
    text = TradingRuntime._format_risk_reasons(["risk:extreme_funding"])
    assert "risk:extreme_funding" not in text


def test_format_signal_reasons_autoaware_contains_timeframe_prefix():
    text = TradingRuntime._format_signal_reasons_autoaware(["1h_primary:trend:1h_long,setup:pullback,trigger:bb_mid_reclaim"])
    assert "1h_primary=>" in text


def test_format_signal_reasons_autoaware_translates_new_codes():
    text = TradingRuntime._format_signal_reasons_autoaware(["1h_primary:confirm:rsi_ok,confirm:volume_ok"])
    assert "RSI确认通过" in text
