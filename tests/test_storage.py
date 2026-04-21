from datetime import UTC, datetime, timedelta
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


def test_list_active_advices_closes_expired_records(tmp_path: Path):
    db = tmp_path / "active.db"
    storage = Storage(db)
    storage.init_schema()
    now = datetime.now(UTC)

    expired_id = "A-BTC-1H-20260420120000-EXPIRED"
    active_id = "A-BTC-1H-20260420130000-ACTIVE"
    storage.insert_advice_record(
        now - timedelta(minutes=60),
        advice_id=expired_id,
        symbol="BTCUSDT",
        side="LONG",
        timeframe_mode="1h_primary",
        payload={"advisory": {"entry_trigger": 62000.0, "valid_minutes": 45}},
    )
    storage.insert_advice_record(
        now - timedelta(minutes=10),
        advice_id=active_id,
        symbol="BTCUSDT",
        side="LONG",
        timeframe_mode="1h_primary",
        payload={"advisory": {"entry_trigger": 62500.0, "valid_minutes": 45}},
    )

    active = storage.list_active_advices(now=now)
    assert len(active) == 1
    assert active[0].advice_id == active_id
    rec_expired = storage.get_advice_record(expired_id)
    assert rec_expired is not None
    assert rec_expired["status"] == "CLOSED"


def test_get_active_advice_filters_symbol_and_side(tmp_path: Path):
    db = tmp_path / "active_filter.db"
    storage = Storage(db)
    storage.init_schema()
    now = datetime.now(UTC)

    storage.insert_advice_record(
        now - timedelta(minutes=5),
        advice_id="A-BTC-1H-20260420150000-LONG1",
        symbol="BTCUSDT",
        side="LONG",
        timeframe_mode="1h_primary",
        payload={"advisory": {"entry_trigger": 63000.0, "valid_minutes": 45}},
    )
    storage.insert_advice_record(
        now - timedelta(minutes=4),
        advice_id="A-BTC-1H-20260420150100-SHORT1",
        symbol="BTCUSDT",
        side="SHORT",
        timeframe_mode="1h_primary",
        payload={"advisory": {"entry_trigger": 62900.0, "valid_minutes": 45}},
    )

    long_advice = storage.get_active_advice(symbol="BTCUSDT", side="LONG", now=now)
    short_advice = storage.get_active_advice(symbol="BTCUSDT", side="SHORT", now=now)
    assert long_advice is not None
    assert short_advice is not None
    assert long_advice.side == "LONG"
    assert short_advice.side == "SHORT"


def test_recent_advice_exists_ignores_closed_records(tmp_path: Path):
    db = tmp_path / "recent.db"
    storage = Storage(db)
    storage.init_schema()
    now = datetime.now(UTC)
    advice_id = "A-BTC-1H-20260420160000-CLOSED1"
    storage.insert_advice_record(
        now - timedelta(minutes=3),
        advice_id=advice_id,
        symbol="BTCUSDT",
        side="LONG",
        timeframe_mode="1h_primary",
        payload={"advisory": {"entry_trigger": 63100.0, "valid_minutes": 45}},
    )
    storage.close_advice_record(advice_id=advice_id, closed_ts=now)
    assert storage.recent_advice_exists(symbol="BTCUSDT", side="LONG", within_minutes=10) is False


def test_get_advice_ids_by_suffix(tmp_path: Path):
    db = tmp_path / "suffix.db"
    storage = Storage(db)
    storage.init_schema()
    now = datetime.now(UTC)
    storage.insert_advice_record(
        now,
        advice_id="A-BTC-1H-20260420160000-ABC123",
        symbol="BTCUSDT",
        side="LONG",
        timeframe_mode="1h_primary",
        payload={"advisory": {"entry_trigger": 63100.0, "valid_minutes": 45}},
    )
    hits = storage.get_advice_ids_by_suffix("abc123", status="OPEN")
    assert hits == ["A-BTC-1H-20260420160000-ABC123"]


def test_get_latest_active_advice(tmp_path: Path):
    db = tmp_path / "latest.db"
    storage = Storage(db)
    storage.init_schema()
    now = datetime.now(UTC)
    storage.insert_advice_record(
        now - timedelta(minutes=7),
        advice_id="A-BTC-1H-20260420160000-OLD111",
        symbol="BTCUSDT",
        side="LONG",
        timeframe_mode="1h_primary",
        payload={"advisory": {"entry_trigger": 63100.0, "valid_minutes": 45}},
    )
    storage.insert_advice_record(
        now - timedelta(minutes=3),
        advice_id="A-ETH-1H-20260420160400-NEW222",
        symbol="ETHUSDT",
        side="SHORT",
        timeframe_mode="1h_primary",
        payload={"advisory": {"entry_trigger": 2900.0, "valid_minutes": 45}},
    )
    latest = storage.get_latest_active_advice(now=now)
    assert latest is not None
    assert latest.advice_id == "A-ETH-1H-20260420160400-NEW222"
