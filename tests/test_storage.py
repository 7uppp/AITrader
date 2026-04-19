from datetime import UTC, datetime
from pathlib import Path

from aitrader.storage import Storage


def test_storage_schema_and_insert_roundtrip(tmp_path: Path):
    db = tmp_path / "test.db"
    storage = Storage(db)
    storage.init_schema()
    now = datetime.now(UTC)
    storage.insert_system_event(now, "unit_test", {"ok": True})
    with storage.connect() as conn:
        row = conn.execute("SELECT event_type, payload_json FROM system_events LIMIT 1").fetchone()
    assert row is not None
    assert row["event_type"] == "unit_test"


def test_trade_feedback_insert_and_stats(tmp_path: Path):
    db = tmp_path / "feedback.db"
    storage = Storage(db)
    storage.init_schema()
    now = datetime.now(UTC)
    storage.insert_trade_feedback(
        ts=now,
        advice_id="A-BTC-1H-1",
        symbol="BTCUSDT",
        outcome="WIN",
        pnl_pct=1.2,
        note="tp_hit",
        payload={"source": "test"},
    )
    storage.insert_trade_feedback(
        ts=now,
        advice_id="A-BTC-1H-2",
        symbol="BTCUSDT",
        outcome="LOSS",
        pnl_pct=-0.6,
        note="sl_hit",
        payload={"source": "test"},
    )
    stats = storage.trade_feedback_stats(symbol="BTCUSDT")
    assert stats["total"] == 2
    assert stats["wins"] == 1
    assert stats["losses"] == 1


def test_advice_registry_roundtrip(tmp_path: Path):
    db = tmp_path / "advice.db"
    storage = Storage(db)
    storage.init_schema()
    now = datetime.now(UTC)
    advice_id = "A-BTC-1H-20260420153012-ABC123"
    storage.insert_advice_record(
        now,
        advice_id=advice_id,
        symbol="BTCUSDT",
        side="LONG",
        timeframe_mode="1h",
        payload={"k": "v"},
    )
    rec = storage.get_advice_record(advice_id)
    assert rec is not None
    assert rec["symbol"] == "BTCUSDT"
    assert rec["status"] == "OPEN"
    assert storage.has_feedback_for_advice(advice_id) is False
    storage.insert_trade_feedback(
        ts=now,
        advice_id=advice_id,
        symbol="BTCUSDT",
        outcome="WIN",
        pnl_pct=0.5,
        note="done",
        payload={"k": "v"},
    )
    assert storage.has_feedback_for_advice(advice_id) is True
    storage.close_advice_record(advice_id=advice_id, closed_ts=now)
    rec2 = storage.get_advice_record(advice_id)
    assert rec2 is not None
    assert rec2["status"] == "CLOSED"
