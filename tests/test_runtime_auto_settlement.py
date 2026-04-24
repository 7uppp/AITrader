from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
import json

from aitrader.runtime import TradingRuntime
from aitrader.storage import Storage
from aitrader.types import LotKind, PositionLot, Side


def _runtime(tmp_path: Path) -> TradingRuntime:
    runtime = TradingRuntime.__new__(TradingRuntime)
    storage = Storage(tmp_path / "runtime_auto_settle.db")
    storage.init_schema()
    runtime.storage = storage
    runtime.position_manager = SimpleNamespace(lots=[])
    runtime.config = SimpleNamespace(
        exchange=SimpleNamespace(kind="hyperliquid"),
        hyperliquid=SimpleNamespace(network="testnet"),
    )
    runtime.notifier = SimpleNamespace(send_text=lambda text: (True, "ok"))
    return runtime


def test_auto_settlement_records_feedback_once(tmp_path: Path):
    runtime = _runtime(tmp_path)
    advice_id = "A-BTC-1H-20260424000100-SET123"
    now = datetime.now(UTC)
    runtime.position_manager.lots = [
        PositionLot(
            symbol="BTCUSDT",
            side=Side.LONG,
            kind=LotKind.MAIN,
            quantity=1.0,
            avg_entry=100.0,
            initial_stop=95.0,
            current_stop=95.0,
            one_r_value=5.0,
            active=False,
            realized_pnl=8.0,
            opened_at=now,
            closed_at=now,
            exit_reason="main:tp_hit",
            exit_executed=True,
            advice_id=advice_id,
        ),
        PositionLot(
            symbol="BTCUSDT",
            side=Side.LONG,
            kind=LotKind.RUNNER,
            quantity=1.0,
            avg_entry=100.0,
            initial_stop=95.0,
            current_stop=100.0,
            one_r_value=5.0,
            active=False,
            realized_pnl=4.0,
            opened_at=now,
            closed_at=now,
            exit_reason="runner:stop_hit",
            exit_executed=True,
            advice_id=advice_id,
        ),
    ]
    ok = TradingRuntime._maybe_finalize_trade_for_advice(runtime, advice_id)
    assert ok is True
    assert runtime.storage.has_feedback_for_advice(advice_id) is True

    latest = runtime.storage.get_latest_trade_feedback(source="auto_trade")
    assert latest is not None
    assert latest["advice_id"] == advice_id
    assert latest["outcome"] == "WIN"
    assert float(latest["pnl_pct"]) == 6.0
    payload = json.loads(str(latest["payload_json"]))
    assert payload["source"] == "auto_trade"
    assert payload["total_pnl_usd"] == 12.0

    second = TradingRuntime._maybe_finalize_trade_for_advice(runtime, advice_id)
    assert second is False
