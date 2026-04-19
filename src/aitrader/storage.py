from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime, timedelta
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import json
import sqlite3


DDL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS market_snapshots (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts TEXT NOT NULL,
      symbol TEXT NOT NULL,
      payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS signal_intents (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts TEXT NOT NULL,
      symbol TEXT NOT NULL,
      side TEXT NOT NULL,
      payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS risk_decisions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts TEXT NOT NULL,
      symbol TEXT NOT NULL,
      approved INTEGER NOT NULL,
      payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS orders (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts TEXT NOT NULL,
      client_order_id TEXT NOT NULL UNIQUE,
      symbol TEXT NOT NULL,
      side TEXT NOT NULL,
      status TEXT NOT NULL,
      payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fills (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts TEXT NOT NULL,
      client_order_id TEXT NOT NULL,
      symbol TEXT NOT NULL,
      quantity REAL NOT NULL,
      price REAL NOT NULL,
      payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS position_lots (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts TEXT NOT NULL,
      symbol TEXT NOT NULL,
      lot_kind TEXT NOT NULL,
      side TEXT NOT NULL,
      active INTEGER NOT NULL,
      payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS system_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts TEXT NOT NULL,
      event_type TEXT NOT NULL,
      payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS operator_commands (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts TEXT NOT NULL,
      command TEXT NOT NULL,
      payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS config_versions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts TEXT NOT NULL,
      version_hash TEXT NOT NULL UNIQUE,
      payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS daily_pnl (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      day TEXT NOT NULL UNIQUE,
      pnl_pct REAL NOT NULL,
      payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS trade_feedback (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts TEXT NOT NULL,
      advice_id TEXT,
      symbol TEXT NOT NULL,
      outcome TEXT NOT NULL,
      pnl_pct REAL,
      note TEXT NOT NULL DEFAULT '',
      payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS advice_registry (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts TEXT NOT NULL,
      advice_id TEXT NOT NULL UNIQUE,
      symbol TEXT NOT NULL,
      side TEXT NOT NULL DEFAULT '',
      timeframe_mode TEXT NOT NULL DEFAULT '',
      status TEXT NOT NULL DEFAULT 'OPEN',
      closed_ts TEXT,
      payload_json TEXT NOT NULL
    )
    """,
]


@dataclass(slots=True)
class Storage:
    path: Path

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_schema(self) -> None:
        with self.connect() as conn:
            for ddl in DDL_STATEMENTS:
                conn.execute(ddl)
            self._ensure_column(conn, table="trade_feedback", column="advice_id", ddl_type="TEXT")
            self._ensure_column(conn, table="advice_registry", column="side", ddl_type="TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, table="advice_registry", column="timeframe_mode", ddl_type="TEXT NOT NULL DEFAULT ''")
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_trade_feedback_advice_id
                ON trade_feedback(advice_id)
                WHERE advice_id IS NOT NULL
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_advice_registry_symbol_status
                ON advice_registry(symbol, status)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_advice_registry_symbol_side_ts
                ON advice_registry(symbol, side, ts)
                """
            )
            conn.commit()

    def insert_market_snapshot(self, ts: datetime, symbol: str, payload: object) -> None:
        self._insert(
            "market_snapshots",
            {"ts": ts.isoformat(), "symbol": symbol, "payload_json": self._to_json(payload)},
        )

    def insert_signal_intent(self, ts: datetime, symbol: str, side: str, payload: object) -> None:
        self._insert(
            "signal_intents",
            {"ts": ts.isoformat(), "symbol": symbol, "side": side, "payload_json": self._to_json(payload)},
        )

    def insert_risk_decision(self, ts: datetime, symbol: str, approved: bool, payload: object) -> None:
        self._insert(
            "risk_decisions",
            {
                "ts": ts.isoformat(),
                "symbol": symbol,
                "approved": 1 if approved else 0,
                "payload_json": self._to_json(payload),
            },
        )

    def insert_order(self, ts: datetime, client_order_id: str, symbol: str, side: str, status: str, payload: object) -> None:
        self._insert(
            "orders",
            {
                "ts": ts.isoformat(),
                "client_order_id": client_order_id,
                "symbol": symbol,
                "side": side,
                "status": status,
                "payload_json": self._to_json(payload),
            },
        )

    def insert_position_lot(self, ts: datetime, symbol: str, lot_kind: str, side: str, active: bool, payload: object) -> None:
        self._insert(
            "position_lots",
            {
                "ts": ts.isoformat(),
                "symbol": symbol,
                "lot_kind": lot_kind,
                "side": side,
                "active": 1 if active else 0,
                "payload_json": self._to_json(payload),
            },
        )

    def insert_system_event(self, ts: datetime, event_type: str, payload: object) -> None:
        self._insert(
            "system_events",
            {"ts": ts.isoformat(), "event_type": event_type, "payload_json": self._to_json(payload)},
        )

    def insert_operator_command(self, ts: datetime, command: str, payload: object) -> None:
        self._insert(
            "operator_commands",
            {"ts": ts.isoformat(), "command": command, "payload_json": self._to_json(payload)},
        )

    def insert_config_version(self, ts: datetime, version_hash: str, payload: object) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO config_versions(ts, version_hash, payload_json)
                VALUES (?, ?, ?)
                ON CONFLICT(version_hash) DO NOTHING
                """,
                (ts.isoformat(), version_hash, self._to_json(payload)),
            )
            conn.commit()

    def upsert_daily_pnl(self, day: date, pnl_pct: float, payload: object) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO daily_pnl(day, pnl_pct, payload_json)
                VALUES (?, ?, ?)
                ON CONFLICT(day) DO UPDATE SET pnl_pct=excluded.pnl_pct, payload_json=excluded.payload_json
                """,
                (day.isoformat(), pnl_pct, self._to_json(payload)),
            )
            conn.commit()

    def insert_trade_feedback(
        self,
        ts: datetime,
        advice_id: str | None,
        symbol: str,
        outcome: str,
        pnl_pct: float | None,
        note: str,
        payload: object,
    ) -> None:
        normalized = outcome.upper()
        if normalized not in {"WIN", "LOSS"}:
            raise ValueError(f"unsupported outcome: {outcome}")
        self._insert(
            "trade_feedback",
            {
                "ts": ts.isoformat(),
                "advice_id": advice_id,
                "symbol": symbol.upper(),
                "outcome": normalized,
                "pnl_pct": pnl_pct,
                "note": note,
                "payload_json": self._to_json(payload),
            },
        )

    def trade_feedback_stats(self, symbol: str | None = None) -> dict[str, float]:
        with self.connect() as conn:
            if symbol:
                row = conn.execute(
                    """
                    SELECT
                      COUNT(*) AS total,
                      SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) AS wins,
                      SUM(CASE WHEN outcome='LOSS' THEN 1 ELSE 0 END) AS losses
                    FROM trade_feedback
                    WHERE symbol = ?
                    """,
                    (symbol.upper(),),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT
                      COUNT(*) AS total,
                      SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) AS wins,
                      SUM(CASE WHEN outcome='LOSS' THEN 1 ELSE 0 END) AS losses
                    FROM trade_feedback
                    """
                ).fetchone()
        total = int(row["total"] or 0)
        wins = int(row["wins"] or 0)
        losses = int(row["losses"] or 0)
        win_rate = (wins / total * 100.0) if total > 0 else 0.0
        return {"total": total, "wins": wins, "losses": losses, "win_rate_pct": win_rate}

    def insert_advice_record(
        self,
        ts: datetime,
        advice_id: str,
        symbol: str,
        side: str,
        timeframe_mode: str,
        payload: object,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO advice_registry(ts, advice_id, symbol, side, timeframe_mode, status, payload_json)
                VALUES (?, ?, ?, ?, ?, 'OPEN', ?)
                ON CONFLICT(advice_id) DO NOTHING
                """,
                (ts.isoformat(), advice_id, symbol.upper(), side.upper(), timeframe_mode, self._to_json(payload)),
            )
            conn.commit()

    def get_advice_record(self, advice_id: str) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT advice_id, symbol, status, ts, closed_ts, payload_json
                FROM advice_registry
                WHERE advice_id = ?
                """,
                (advice_id,),
            ).fetchone()

    def close_advice_record(self, advice_id: str, closed_ts: datetime) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE advice_registry
                SET status='CLOSED', closed_ts=?
                WHERE advice_id = ?
                """,
                (closed_ts.isoformat(), advice_id),
            )
            conn.commit()

    def has_feedback_for_advice(self, advice_id: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM trade_feedback WHERE advice_id = ? LIMIT 1",
                (advice_id,),
            ).fetchone()
        return row is not None

    def recent_advice_exists(self, symbol: str, side: str, within_minutes: int) -> bool:
        if within_minutes <= 0:
            return False
        cutoff_ts = (datetime.now(UTC) - timedelta(minutes=within_minutes)).isoformat()
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM advice_registry
                WHERE symbol = ?
                  AND side = ?
                  AND ts >= ?
                LIMIT 1
                """,
                (symbol.upper(), side.upper(), cutoff_ts),
            ).fetchone()
        return row is not None

    def _insert(self, table: str, row: dict[str, object]) -> None:
        cols = list(row.keys())
        placeholders = ", ".join("?" for _ in cols)
        col_sql = ", ".join(cols)
        values = [row[col] for col in cols]
        with self.connect() as conn:
            conn.execute(f"INSERT INTO {table}({col_sql}) VALUES ({placeholders})", values)
            conn.commit()

    def _to_json(self, payload: object) -> str:
        return json.dumps(self._jsonable(payload), separators=(",", ":"), ensure_ascii=False)

    def _jsonable(self, payload: object) -> object:
        if is_dataclass(payload):
            payload = asdict(payload)
        if isinstance(payload, datetime):
            return payload.isoformat()
        if isinstance(payload, date):
            return payload.isoformat()
        if isinstance(payload, Enum):
            return payload.value
        if isinstance(payload, Path):
            return str(payload)
        if isinstance(payload, dict):
            return {k: self._jsonable(v) for k, v in payload.items()}
        if isinstance(payload, list):
            return [self._jsonable(v) for v in payload]
        return payload

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl_type: str) -> None:
        cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
        existing = {str(c[1]) for c in cols}
        if column in existing:
            return
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")
