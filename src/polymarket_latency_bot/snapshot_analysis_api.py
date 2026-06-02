from __future__ import annotations

from collections import Counter
from statistics import mean
from typing import Any

from fastapi import FastAPI, Query

from .models import now_ms


def _ratio(values: list[bool]) -> float:
    return round(sum(1 for value in values if value) / max(1, len(values)), 6)


def _snapshot_trend(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    if not snapshots:
        return {
            "snapshot_count": 0,
            "duration_sec": 0.0,
            "realized_pnl_delta": 0.0,
            "directions": {},
            "queue": {"average": 0.0, "maximum": 0},
            "connections": {"market_ws_uptime": 0.0, "rtds_ws_uptime": 0.0},
            "fusion_ready_ratio": 0.0,
        }

    ordered = sorted(snapshots, key=lambda item: int(item.get("timestamp_ms") or 0))
    first = ordered[0]
    last = ordered[-1]
    first_ts = int(first.get("timestamp_ms") or 0)
    last_ts = int(last.get("timestamp_ms") or 0)
    queue_depths = [int(item.get("queue_depth") or 0) for item in ordered]
    directions = Counter(str((item.get("ai") or {}).get("direction") or "WAIT") for item in ordered)
    market_ws = [bool((item.get("connections") or {}).get("market_ws")) for item in ordered]
    rtds_ws = [bool((item.get("connections") or {}).get("rtds_ws")) for item in ordered]
    fusion_ready = [str((item.get("fusion") or {}).get("status") or "") == "ready" for item in ordered]
    first_pnl = float((first.get("paper") or {}).get("realized_pnl") or 0.0)
    last_pnl = float((last.get("paper") or {}).get("realized_pnl") or 0.0)

    return {
        "snapshot_count": len(ordered),
        "first_timestamp_ms": first_ts,
        "last_timestamp_ms": last_ts,
        "duration_sec": round(max(0, last_ts - first_ts) / 1000, 3),
        "realized_pnl_first": round(first_pnl, 8),
        "realized_pnl_last": round(last_pnl, 8),
        "realized_pnl_delta": round(last_pnl - first_pnl, 8),
        "directions": dict(sorted(directions.items())),
        "queue": {
            "average": round(float(mean(queue_depths)), 4),
            "maximum": max(queue_depths),
        },
        "connections": {
            "market_ws_uptime": _ratio(market_ws),
            "rtds_ws_uptime": _ratio(rtds_ws),
        },
        "fusion_ready_ratio": _ratio(fusion_ready),
    }


def register_snapshot_analysis_routes(app: FastAPI, recorder: Any) -> None:
    @app.get("/snapshots/trend")
    async def snapshots_trend(limit: int = Query(default=120, ge=1, le=1000)) -> dict[str, Any]:
        snapshots = recorder.store.recent(limit)
        return {
            "profile": {"name": "balanced_btc5m_hf", "version": "2026-06-02.1"},
            "trend": _snapshot_trend(snapshots),
        }

    @app.get("/snapshots/health")
    async def snapshots_health() -> dict[str, Any]:
        latest = recorder.store.recent(1)
        if not latest:
            return {
                "healthy": False,
                "reason": "no_snapshots_yet",
                "snapshot_count": recorder.store.count(),
                "last_recorded_ms": recorder.last_recorded_ms,
            }
        last_recorded_ms = int(latest[0].get("timestamp_ms") or 0)
        age_ms = max(0, now_ms() - last_recorded_ms)
        healthy = age_ms <= max(180000, int(recorder.interval_sec * 3000))
        return {
            "healthy": healthy,
            "reason": "ok" if healthy else "snapshots_stale",
            "snapshot_count": recorder.store.count(),
            "last_recorded_ms": last_recorded_ms,
            "age_ms": age_ms,
            "maximum_expected_age_ms": max(180000, int(recorder.interval_sec * 3000)),
        }
