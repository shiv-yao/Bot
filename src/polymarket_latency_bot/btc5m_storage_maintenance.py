from __future__ import annotations

import os
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse


NO_STORE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _safe_file_size(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except OSError:
        return 0


def _directory_inventory(directory: Path, *, limit: int = 20) -> list[dict[str, Any]]:
    try:
        rows = [
            {"name": path.name, "size_bytes": _safe_file_size(path)}
            for path in directory.iterdir()
            if path.is_file()
        ]
    except OSError:
        return []
    rows.sort(key=lambda row: int(row["size_bytes"]), reverse=True)
    return rows[: max(1, int(limit))]


class BTC5mStorageMaintenance:
    """Bound SQLite history growth and expose read-only volume diagnostics.

    Only the active BTC 5m table is pruned. Unknown files are listed for
    diagnostics but are never deleted automatically. Between maintenance
    windows, all status reads are served from memory to avoid slowing the
    prediction loop with repeated SQLite queries.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = str(db_path)
        self.database = Path(self.db_path)
        self.data_dir = self.database.parent
        self.retain_rounds = max(100, int(os.getenv("BTC5M_STORAGE_RETAIN_ROUNDS", "2500")))
        self.maintenance_interval_sec = max(60, int(os.getenv("BTC5M_STORAGE_MAINTENANCE_INTERVAL_SEC", "900")))
        self.vacuum_min_deleted_rows = max(1, int(os.getenv("BTC5M_STORAGE_VACUUM_MIN_DELETED_ROWS", "100")))
        self.vacuum_min_interval_sec = max(3600, int(os.getenv("BTC5M_STORAGE_VACUUM_MIN_INTERVAL_SEC", "21600")))
        self.last_run_ms = 0
        self.last_vacuum_ms = 0
        self.deleted_rows_total = 0
        self.last_error: str | None = None
        self.last_report: dict[str, Any] = {}

    def _connect(self) -> sqlite3.Connection:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _volume_usage(self) -> dict[str, Any]:
        try:
            usage = shutil.disk_usage(self.data_dir)
            total = int(usage.total)
            used = int(usage.used)
            free = int(usage.free)
            percent = round(used / max(1, total) * 100, 2)
            return {
                "volume_total_bytes": total,
                "volume_used_bytes": used,
                "volume_free_bytes": free,
                "volume_used_percent": percent,
            }
        except OSError as exc:
            return {
                "volume_total_bytes": None,
                "volume_used_bytes": None,
                "volume_free_bytes": None,
                "volume_used_percent": None,
                "volume_error": str(exc),
            }

    def _report(self, *, row_count: int | None = None, deleted_rows: int = 0, vacuumed: bool = False) -> dict[str, Any]:
        if row_count is None:
            try:
                with self._connect() as db:
                    row_count = int(db.execute("SELECT COUNT(*) FROM btc5m_round_predictions").fetchone()[0])
            except (sqlite3.Error, OSError):
                row_count = None
        report = {
            "ok": self.last_error is None,
            "database": self.db_path,
            "table": "btc5m_round_predictions",
            "retention_rounds": self.retain_rounds,
            "row_count": row_count,
            "deleted_rows_last_run": int(deleted_rows),
            "deleted_rows_total": int(self.deleted_rows_total),
            "maintenance_interval_sec": self.maintenance_interval_sec,
            "vacuumed_last_run": bool(vacuumed),
            "last_run_ms": self.last_run_ms or None,
            "last_vacuum_ms": self.last_vacuum_ms or None,
            "database_size_bytes": _safe_file_size(self.database),
            "wal_size_bytes": _safe_file_size(Path(f"{self.db_path}-wal")),
            "shm_size_bytes": _safe_file_size(Path(f"{self.db_path}-shm")),
            "last_error": self.last_error,
            "files": _directory_inventory(self.data_dir),
            "note": "Only the active BTC 5m table is pruned automatically. Unknown files are never deleted.",
            **self._volume_usage(),
        }
        self.last_report = report
        _publish_storage_health(report)
        return dict(report)

    def maybe_run(self, *, force: bool = False) -> dict[str, Any]:
        timestamp = _now_ms()
        if not force and self.last_run_ms and timestamp - self.last_run_ms < self.maintenance_interval_sec * 1000:
            return self.get_cached_report()

        deleted_rows = 0
        vacuumed = False
        row_count: int | None = None
        self.last_error = None
        self.last_run_ms = timestamp
        try:
            with self._connect() as db:
                row_count = int(db.execute("SELECT COUNT(*) FROM btc5m_round_predictions").fetchone()[0])
                if row_count > self.retain_rounds:
                    deleted_rows = int(
                        db.execute(
                            """
                            DELETE FROM btc5m_round_predictions
                            WHERE slug NOT IN (
                                SELECT slug FROM btc5m_round_predictions
                                ORDER BY interval_start_ms DESC
                                LIMIT ?
                            )
                            """,
                            (self.retain_rounds,),
                        ).rowcount
                    )
                    db.commit()
                    row_count = int(db.execute("SELECT COUNT(*) FROM btc5m_round_predictions").fetchone()[0])
                db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self.deleted_rows_total += max(0, deleted_rows)
            vacuum_due = (
                deleted_rows >= self.vacuum_min_deleted_rows
                and (not self.last_vacuum_ms or timestamp - self.last_vacuum_ms >= self.vacuum_min_interval_sec * 1000)
            )
            if vacuum_due:
                with self._connect() as db:
                    db.execute("VACUUM")
                self.last_vacuum_ms = timestamp
                vacuumed = True
        except (sqlite3.Error, OSError) as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
        return self._report(row_count=row_count, deleted_rows=deleted_rows, vacuumed=vacuumed)

    def get_cached_report(self) -> dict[str, Any]:
        if self.last_report:
            return dict(self.last_report)
        return self._report()


_latest_storage_health: dict[str, Any] = {
    "ok": False,
    "status": "initializing",
    "note": "Waiting for the BTC 5m storage maintenance layer to initialize.",
}


def _publish_storage_health(report: dict[str, Any]) -> None:
    global _latest_storage_health
    _latest_storage_health = dict(report)


def get_storage_health() -> dict[str, Any]:
    return dict(_latest_storage_health)


def register_btc5m_storage_health(app: FastAPI) -> None:
    @app.get("/storage-health")
    async def storage_health() -> JSONResponse:
        return JSONResponse(get_storage_health(), headers=NO_STORE_HEADERS)
