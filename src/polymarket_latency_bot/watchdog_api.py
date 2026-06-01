from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse


def _metric_name(value: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in value.lower()).strip("_")


def register_watchdog_routes(app: FastAPI, watchdog: Any) -> None:
    @app.get("/watchdog")
    async def watchdog_status() -> dict[str, Any]:
        return watchdog.snapshot()

    @app.get("/alerts")
    async def alerts() -> dict[str, Any]:
        snapshot = watchdog.snapshot()
        return {"status": snapshot.get("status", "unknown"), "alerts": snapshot.get("alerts", [])}

    @app.get("/metrics/runtime")
    async def runtime_metrics() -> dict[str, Any]:
        snapshot = await watchdog.state.snapshot()
        return {
            "queue_depth": snapshot.get("queue_depth", 0),
            "queue_high_water": snapshot.get("queue_high_water", 0),
            "orders_submitted": snapshot.get("orders_submitted", 0),
            "orders_rejected": snapshot.get("orders_rejected", 0),
            "throughput": snapshot.get("throughput", {}),
            "latency": snapshot.get("latency", {}),
            "watchdog_status": watchdog.last_status,
        }

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        snapshot = watchdog.snapshot()
        status = snapshot.get("status", "unknown")
        return {"ok": status != "error", "status": status}

    @app.get("/metrics/prometheus", response_class=PlainTextResponse)
    async def prometheus_metrics() -> PlainTextResponse:
        snapshot = await watchdog.state.snapshot()
        lines = [
            "# TYPE bot_queue_depth gauge",
            f"bot_queue_depth {snapshot.get('queue_depth', 0)}",
            "# TYPE bot_queue_high_water gauge",
            f"bot_queue_high_water {snapshot.get('queue_high_water', 0)}",
            "# TYPE bot_orders_submitted counter",
            f"bot_orders_submitted {snapshot.get('orders_submitted', 0)}",
            "# TYPE bot_orders_rejected counter",
            f"bot_orders_rejected {snapshot.get('orders_rejected', 0)}",
        ]
        for name, metric in (snapshot.get("throughput", {}) or {}).items():
            safe = _metric_name(name)
            lines.append(f"bot_throughput_last_60s{{event=\"{safe}\"}} {metric.get('last_60s', 0)}")
            lines.append(f"bot_throughput_per_sec{{event=\"{safe}\"}} {metric.get('per_sec', 0)}")
        for name, metric in (snapshot.get("latency", {}) or {}).items():
            safe = _metric_name(name)
            lines.append(f"bot_latency_p50_ms{{metric=\"{safe}\"}} {metric.get('p50_ms', 0)}")
            lines.append(f"bot_latency_p95_ms{{metric=\"{safe}\"}} {metric.get('p95_ms', 0)}")
            lines.append(f"bot_latency_p99_ms{{metric=\"{safe}\"}} {metric.get('p99_ms', 0)}")
        return PlainTextResponse("\n".join(lines) + "\n")
