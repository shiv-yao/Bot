from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any


class PaperStore:
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
                CREATE TABLE IF NOT EXISTS paper_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_id TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    notional_usd REAL NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL NOT NULL,
                    shares REAL NOT NULL,
                    realized_pnl REAL NOT NULL,
                    opened_ms INTEGER NOT NULL,
                    closed_ms INTEGER NOT NULL,
                    hold_ms INTEGER NOT NULL,
                    close_reason TEXT NOT NULL,
                    market_slug TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            db.execute("CREATE INDEX IF NOT EXISTS idx_paper_trades_closed_ms ON paper_trades(closed_ms)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_paper_trades_market_slug ON paper_trades(market_slug)")

    def record_trade(self, trade: Any) -> None:
        payload = asdict(trade)
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO paper_trades (
                    token_id, direction, notional_usd, entry_price, exit_price,
                    shares, realized_pnl, opened_ms, closed_ms, hold_ms,
                    close_reason, market_slug, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["token_id"], payload["direction"], payload["notional_usd"],
                    payload["entry_price"], payload["exit_price"], payload["shares"],
                    payload["realized_pnl"], payload["opened_ms"], payload["closed_ms"],
                    payload["hold_ms"], payload["close_reason"], payload["market_slug"],
                    json.dumps(payload, separators=(",", ":")),
                ),
            )

    def recent_trades(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT payload_json FROM paper_trades ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def summary(self) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT
                    COUNT(*) AS closed_trades,
                    COALESCE(SUM(realized_pnl), 0) AS realized_pnl,
                    COALESCE(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END), 0) AS wins,
                    COALESCE(SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END), 0) AS losses,
                    COALESCE(SUM(CASE WHEN realized_pnl = 0 THEN 1 ELSE 0 END), 0) AS flat
                FROM paper_trades
                """
            ).fetchone()
        return dict(row) if row else {"closed_trades": 0, "realized_pnl": 0.0, "wins": 0, "losses": 0, "flat": 0}
