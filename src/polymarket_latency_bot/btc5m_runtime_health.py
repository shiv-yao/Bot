from __future__ import annotations

import time
from typing import Any

from fastapi import FastAPI


STALE_BOOK_THRESHOLD_MS = 5000


def _now_ms() -> int:
    return int(time.time() * 1000)


def build_runtime_health(payload: dict[str, Any], *, timestamp_ms: int | None = None) -> dict[str, Any]:
    """Summarize runtime freshness and source health from the public status payload.

    This is read-only diagnostics. It never places orders, changes thresholds or
    mutates the trading engine.
    """

    timestamp = int(timestamp_ms if timestamp_ms is not None else _now_ms())
    market = payload.get("market", {}) or {}
    sources = payload.get("sources", {}) or {}
    fusion = payload.get("fusion", {}) or {}

    source_health = {
        str(name): {
            "connected": bool((row or {}).get("connected")),
            "last_error": (row or {}).get("last_error"),
        }
        for name, row in sources.items()
    }
    connected_sources = sum(1 for row in source_health.values() if row["connected"])
    clean_sources = int(fusion.get("clean_source_count", fusion.get("source_count", 0)) or 0)
    fusion_status = str(fusion.get("status") or "waiting_for_sources")

    raw_book_ages = [market.get("yes_book_age_ms"), market.get("no_book_age_ms")]
    book_ages = [int(value) for value in raw_book_ages if isinstance(value, (int, float))]
    oldest_book_age_ms = max(book_ages) if book_ages else None
    stale_data = oldest_book_age_ms is not None and oldest_book_age_ms > STALE_BOOK_THRESHOLD_MS
    market_ready = market.get("discovery_status") == "ready"
    degraded = not market_ready or fusion_status != "ready" or clean_sources < 2 or connected_sources < 2

    if stale_data:
        status = "stale_data"
    elif degraded:
        status = "degraded"
    else:
        status = "active"

    return {
        "ok": status == "active",
        "status": status,
        "updated_at_ms": timestamp,
        "market_ready": market_ready,
        "connected_sources": connected_sources,
        "clean_sources": clean_sources,
        "fusion_status": fusion_status,
        "oldest_book_age_ms": oldest_book_age_ms,
        "stale_book_threshold_ms": STALE_BOOK_THRESHOLD_MS,
        "source_health": source_health,
        "note": "Read-only runtime diagnostics. This endpoint never places orders or changes settings.",
    }


_latest_runtime_health: dict[str, Any] = {
    "ok": False,
    "status": "initializing",
    "updated_at_ms": 0,
    "market_ready": False,
    "connected_sources": 0,
    "clean_sources": 0,
    "fusion_status": "initializing",
    "oldest_book_age_ms": None,
    "stale_book_threshold_ms": STALE_BOOK_THRESHOLD_MS,
    "source_health": {},
    "note": "Waiting for the first /status evaluation.",
}


def update_runtime_health(payload: dict[str, Any]) -> dict[str, Any]:
    global _latest_runtime_health
    _latest_runtime_health = build_runtime_health(payload)
    return dict(_latest_runtime_health)


def get_runtime_health() -> dict[str, Any]:
    return dict(_latest_runtime_health)


def register_btc5m_runtime_health(app: FastAPI) -> None:
    @app.get("/runtime-health")
    async def runtime_health() -> dict[str, Any]:
        return get_runtime_health()
