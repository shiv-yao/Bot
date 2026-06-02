from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from .models import now_ms


PROFILE_NAME = "balanced_btc5m_hf"
PROFILE_VERSION = "2026-06-02.1"


def _to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    data = getattr(value, "__dict__", None)
    if isinstance(data, dict):
        return dict(data)
    return {"value": str(value)}


class RuntimeSnapshotStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        path = Path(db_path)
        if path.parent and str(path.parent) != ".":
            path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_runtime_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp_ms INTEGER NOT NULL,
                    profile_name TEXT NOT NULL,
                    profile_version TEXT NOT NULL,
                    market_slug TEXT NOT NULL,
                    ai_direction TEXT NOT NULL,
                    realized_pnl REAL NOT NULL,
                    unrealized_pnl REAL NOT NULL,
                    queue_depth INTEGER NOT NULL,
                    risk_halted INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_runtime_snapshots_timestamp ON paper_runtime_snapshots(timestamp_ms)"
            )

    def record(self, payload: dict[str, Any]) -> None:
        market = payload.get("market") or {}
        ai = payload.get("ai") or {}
        paper = payload.get("paper") or {}
        risk = payload.get("risk") or {}
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO paper_runtime_snapshots (
                    timestamp_ms, profile_name, profile_version, market_slug,
                    ai_direction, realized_pnl, unrealized_pnl, queue_depth,
                    risk_halted, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(payload["timestamp_ms"]),
                    PROFILE_NAME,
                    PROFILE_VERSION,
                    str(market.get("slug") or ""),
                    str(ai.get("direction") or "WAIT"),
                    float(paper.get("realized_pnl") or 0.0),
                    float(paper.get("unrealized_pnl") or 0.0),
                    int(payload.get("queue_depth") or 0),
                    1 if risk.get("halted") else 0,
                    json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
                ),
            )

    def recent(self, limit: int = 120) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT payload_json FROM paper_runtime_snapshots ORDER BY id DESC LIMIT ?",
                (max(1, min(int(limit), 1000)),),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def count(self) -> int:
        with self._connect() as db:
            row = db.execute("SELECT COUNT(*) AS count FROM paper_runtime_snapshots").fetchone()
        return int(row["count"] if row else 0)


class RuntimeSnapshotRecorder:
    def __init__(self, state: Any, risk: Any, portfolio: Any, interval_sec: float = 60.0) -> None:
        self.state = state
        self.risk = risk
        self.portfolio = portfolio
        self.interval_sec = max(10.0, float(interval_sec))
        self.store = RuntimeSnapshotStore(str(portfolio.store.db_path))
        self.last_recorded_ms: int | None = None

    async def capture(self) -> dict[str, Any]:
        snapshot = await self.state.snapshot()
        async with self.risk.lock:
            risk_snapshot = _to_dict(self.risk.snapshot)
        paper_summary = (snapshot.get("paper_portfolio") or {}).get("summary", {})
        strategy = snapshot.get("last_strategy_snapshot") or {}
        payload = {
            "timestamp_ms": now_ms(),
            "profile": {"name": PROFILE_NAME, "version": PROFILE_VERSION},
            "market": snapshot.get("current_market") or {},
            "ai": {
                "direction": strategy.get("direction", "WAIT"),
                "decision": strategy.get("decision"),
                "fair_probability_up": strategy.get("fair_probability_up"),
                "confidence": strategy.get("confidence"),
                "reason": strategy.get("reason"),
            },
            "paper": paper_summary,
            "risk": risk_snapshot,
            "queue_depth": snapshot.get("queue_depth", 0),
            "queue_high_water": snapshot.get("queue_high_water", 0),
            "throughput": snapshot.get("throughput", {}),
            "latency": snapshot.get("latency", {}),
            "connections": snapshot.get("connections", {}),
            "fusion": snapshot.get("fusion_snapshot", {}),
        }
        self.store.record(payload)
        self.last_recorded_ms = int(payload["timestamp_ms"])
        return payload

    async def loop(self) -> None:
        while True:
            try:
                await self.capture()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Snapshot failures must never stop the Paper trading loop.
                pass
            await asyncio.sleep(self.interval_sec)


def register_snapshot_routes(app: FastAPI, recorder: RuntimeSnapshotRecorder) -> None:
    @app.get("/profile/status")
    async def profile_status() -> dict[str, Any]:
        return {
            "mode": "paper",
            "profile": {"name": PROFILE_NAME, "version": PROFILE_VERSION},
            "database": str(recorder.store.db_path),
            "snapshot_interval_sec": recorder.interval_sec,
        }

    @app.get("/snapshots/status")
    async def snapshots_status() -> dict[str, Any]:
        return {
            "profile": {"name": PROFILE_NAME, "version": PROFILE_VERSION},
            "database": str(recorder.store.db_path),
            "snapshot_interval_sec": recorder.interval_sec,
            "snapshot_count": recorder.store.count(),
            "last_recorded_ms": recorder.last_recorded_ms,
        }

    @app.get("/snapshots/recent")
    async def snapshots_recent(limit: int = 120) -> dict[str, Any]:
        return {
            "profile": {"name": PROFILE_NAME, "version": PROFILE_VERSION},
            "snapshots": recorder.store.recent(limit),
        }
