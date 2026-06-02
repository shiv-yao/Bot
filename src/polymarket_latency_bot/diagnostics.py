from __future__ import annotations

from pathlib import Path
from typing import Any


def build_diagnostics(settings: Any, snapshot: dict[str, Any], risk_snapshot: Any, db_path: str) -> dict[str, Any]:
    warnings: list[dict[str, str]] = []
    connections = snapshot.get("connections", {})
    sources = snapshot.get("source_status", {})
    fusion = snapshot.get("fusion_snapshot", {})
    strategy = snapshot.get("last_strategy_snapshot", {}) or {}
    queue_depth = int(snapshot.get("queue_depth", 0))
    queue_high_water = int(snapshot.get("queue_high_water", 0))
    interval_sec = int(getattr(settings, "market_interval_sec", 0))
    slug_prefix = str(getattr(settings, "market_slug_prefix", ""))
    db_filename = Path(str(db_path)).name
    isolated_db = db_filename == "polymarket_paper_btc5m_balanced.db"

    if not connections.get("market_ws"):
        warnings.append({"level": "error", "code": "market_ws_offline", "message": "Polymarket Market WS is offline"})
    if not connections.get("rtds_ws"):
        warnings.append({"level": "error", "code": "rtds_ws_offline", "message": "Chainlink RTDS is offline"})
    if snapshot.get("market_discovery_status") != "ready":
        warnings.append({"level": "warning", "code": "market_not_ready", "message": "BTC 5-minute market discovery is not ready"})
    if interval_sec != 300:
        warnings.append({"level": "error", "code": "market_interval_not_5m", "message": f"Expected BTC 5-minute interval, got {interval_sec} seconds"})
    if slug_prefix != "btc-updown-5m-":
        warnings.append({"level": "error", "code": "market_slug_not_5m", "message": f"Expected btc-updown-5m- slug prefix, got {slug_prefix}"})
    if fusion.get("status") != "ready":
        warnings.append({"level": "warning", "code": "fusion_not_ready", "message": f"Fusion status is {fusion.get('status', 'unknown')}"})

    max_age_ms = int(getattr(settings, "external_price_max_age_ms", 3000))
    for source, status in sources.items():
        if not status.get("connected"):
            warnings.append({"level": "warning", "code": f"source_offline:{source}", "message": f"{source} source is offline"})
            continue
        age_ms = status.get("age_ms")
        if isinstance(age_ms, (int, float)) and age_ms > max_age_ms:
            warnings.append({"level": "warning", "code": f"source_stale:{source}", "message": f"{source} source is stale: {int(age_ms)} ms"})

    max_queue = max(1, int(getattr(settings, "max_queue_size", 1000)))
    if queue_depth >= max_queue * 0.8:
        warnings.append({"level": "error", "code": "queue_near_capacity", "message": f"Queue depth is {queue_depth}/{max_queue}"})
    elif queue_depth >= max_queue * 0.5:
        warnings.append({"level": "warning", "code": "queue_elevated", "message": f"Queue depth is {queue_depth}/{max_queue}"})

    secret = str(getattr(settings, "webhook_secret", ""))
    secret_ready = bool(secret and secret != "change-me" and len(secret) >= 16)
    if not secret_ready:
        warnings.append({"level": "warning", "code": "webhook_secret_not_configured", "message": "Write APIs remain disabled until WEBHOOK_SECRET is customized"})
    if bool(getattr(settings, "live_enabled", False)):
        warnings.append({"level": "error", "code": "live_enabled", "message": "Live mode is enabled; this branch is intended for Paper validation"})
    if not str(db_path).startswith("/data/"):
        warnings.append({"level": "warning", "code": "volume_not_durable", "message": f"Paper database is not using /data volume: {db_path}"})
    if not isolated_db:
        warnings.append({"level": "warning", "code": "history_not_isolated", "message": f"Expected isolated BTC 5m history database, got {db_filename}"})
    if bool(getattr(risk_snapshot, "halted", False)):
        warnings.append({"level": "warning", "code": "risk_halted", "message": f"Risk manager halted: {getattr(risk_snapshot, 'halt_reason', '')}"})

    errors = sum(1 for item in warnings if item["level"] == "error")
    status = "error" if errors else "warning" if warnings else "healthy"
    return {
        "status": status,
        "profile": "balanced_btc5m_hf" if not bool(getattr(settings, "live_enabled", False)) else "live_baseline",
        "warnings": warnings,
        "market": {
            "interval_sec": interval_sec,
            "interval_minutes": round(interval_sec / 60, 2) if interval_sec else 0,
            "slug_prefix": slug_prefix,
            "discovery_status": snapshot.get("market_discovery_status"),
            "current_market": snapshot.get("current_market"),
        },
        "ai_decision": {
            "mode": strategy.get("ai_mode", "single_direction_yes_no"),
            "decision": strategy.get("decision"),
            "direction": strategy.get("direction", "WAIT"),
            "fair_probability_up": strategy.get("fair_probability_up"),
            "confidence": strategy.get("confidence"),
            "reason": strategy.get("reason"),
        },
        "history": {
            "db_path": str(db_path),
            "db_filename": db_filename,
            "is_btc5m_isolated": isolated_db,
            "legacy_database_preserved": "/data/polymarket_paper.db",
        },
        "queue": {"depth": queue_depth, "high_water": queue_high_water, "capacity": max_queue},
        "throughput": snapshot.get("throughput", {}),
        "latency": snapshot.get("latency", {}),
        "fusion": fusion,
        "connections": connections,
        "source_status": sources,
        "security": {"webhook_secret_configured": secret_ready, "live_enabled": bool(getattr(settings, "live_enabled", False)), "db_path": str(db_path)},
    }
