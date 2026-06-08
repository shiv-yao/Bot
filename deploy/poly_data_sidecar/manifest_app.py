from __future__ import annotations

import csv
import json
import os
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse


NO_STORE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _safe_size(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except OSError:
        return 0


def _safe_mtime_ms(path: Path) -> int | None:
    try:
        return int(path.stat().st_mtime * 1000)
    except OSError:
        return None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return dict(payload) if isinstance(payload, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _csv_header(path: Path) -> list[str]:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return list(next(csv.reader(handle), []))
    except (OSError, StopIteration):
        return []


def _secret_ready(value: str) -> bool:
    return bool(value and value != "change-me" and len(value) >= 16)


def build_manifest(*, timestamp_ms: int | None = None) -> dict[str, Any]:
    timestamp = int(timestamp_ms if timestamp_ms is not None else _now_ms())
    root = Path(os.getenv("POLY_DATA_SIDECAR_ROOT", "/data/poly_data"))
    markets = Path(os.getenv("POLY_DATA_MARKETS_PATH", str(root / "data" / "markets.csv")))
    orders = Path(os.getenv("POLY_DATA_ORDERS_PATH", str(root / "data" / "orderFilled.csv")))
    trades = Path(os.getenv("POLY_DATA_TRADES_PATH", str(root / "processed" / "trades.csv")))
    cursor = Path(os.getenv("POLY_DATA_CURSOR_PATH", str(root / "data" / "cursor_state.json")))
    max_stale_sec = max(60, int(os.getenv("POLY_DATA_MAX_STALE_SEC", "900")))
    paths = {"markets": markets, "orders": orders, "trades": trades, "cursor": cursor}
    files: dict[str, Any] = {}
    newest_mtime_ms: int | None = None
    for name, path in paths.items():
        modified_ms = _safe_mtime_ms(path)
        if modified_ms is not None:
            newest_mtime_ms = modified_ms if newest_mtime_ms is None else max(newest_mtime_ms, modified_ms)
        files[name] = {
            "path": str(path),
            "exists": path.exists(),
            "size_bytes": _safe_size(path),
            "modified_ms": modified_ms,
        }
    required_present = markets.exists() and orders.exists() and trades.exists()
    latest_file_age_ms = timestamp - newest_mtime_ms if newest_mtime_ms is not None else None
    stale = latest_file_age_ms is None or latest_file_age_ms > max_stale_sec * 1000
    if not required_present:
        status = "waiting_for_sidecar_files"
    elif stale:
        status = "stale"
    else:
        status = "ready"
    return {
        "ok": status == "ready",
        "ready": status == "ready",
        "status": status,
        "updated_at_ms": newest_mtime_ms or 0,
        "checked_at_ms": timestamp,
        "latest_file_age_ms": latest_file_age_ms,
        "max_stale_sec": max_stale_sec,
        "repository": "https://github.com/warproxxx/poly_data.git",
        "integration_mode": "separate_sidecar_manifest_read_only",
        "cursor": _read_json(cursor),
        "schemas": {
            "markets": _csv_header(markets),
            "orders": _csv_header(orders),
            "trades": _csv_header(trades),
        },
        "files": files,
        "safety": {
            "read_only": True,
            "places_orders": False,
            "wallet_signing": False,
            "mutates_upstream_files": False,
        },
        "note": "Manifest metadata only. This endpoint never places orders or signs wallets.",
    }


app = FastAPI(title="poly_data read-only sidecar manifest")


def _authorize(authorization: str | None) -> None:
    expected = os.getenv("POLY_DATA_SIDECAR_MANIFEST_TOKEN", "").strip()
    if not _secret_ready(expected):
        return
    if str(authorization or "") != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="invalid manifest token")


@app.get("/healthz")
async def healthz() -> JSONResponse:
    return JSONResponse({"ok": True, "service": "poly_data_sidecar_manifest"}, headers=NO_STORE_HEADERS)


@app.get("/manifest")
async def manifest(authorization: str | None = Header(default=None)) -> JSONResponse:
    _authorize(authorization)
    return JSONResponse(build_manifest(), headers=NO_STORE_HEADERS)
