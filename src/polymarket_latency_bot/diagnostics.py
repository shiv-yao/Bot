from __future__ import annotations

from typing import Any


def build_diagnostics(settings: Any, snapshot: dict[str, Any], risk_snapshot: Any, db_path: str) -> dict[str, Any]:
    warnings: list[dict[str, str]] = []
    connections = snapshot.get("connections", {})
    sources = snapshot.get("source_status", {})
    fusion = snapshot.get("fusion_snapshot", {})
    queue_depth = int(snapshot.get("queue_depth", 0))
    queue_high_water = int(snapshot.get("queue_high_water", 0))

    if not connections.get("market_ws"):
        warnings.append({"level": "error", "code": "market_ws_offline", "message": "Polymarket Market WS is offline"})
    if not connections.get("rtds_ws"):
        warnings.append({"level": "error", "code": "rtds_ws_offline", "message": "Chainlink RTDS is offline"})
    if snapshot.get("market_discovery_status") != "ready":
        warnings.append({"level": "warning", "code": "market_not_ready", "message": "BTC 15-minute market discovery is not ready"})
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
    if bool(getattr(risk_snapshot, "halted", False)):
        warnings.append({"level": "warning", "code": "risk_halted", "message": f"Risk manager halted: {getattr(risk_snapshot, 'halt_reason', '')}"})

    errors = sum(1 for item in warnings if item["level"] == "error")
    status = "error" if errors else "warning" if warnings else "healthy"
    return {
        "status": status,
        "warnings": warnings,
        "queue": {"depth": queue_depth, "high_water": queue_high_water, "capacity": max_queue},
        "throughput": snapshot.get("throughput", {}),
        "latency": snapshot.get("latency", {}),
        "fusion": fusion,
        "connections": connections,
        "source_status": sources,
        "security": {"webhook_secret_configured": secret_ready, "live_enabled": bool(getattr(settings, "live_enabled", False)), "db_path": db_path},
    }
