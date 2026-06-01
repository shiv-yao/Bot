from __future__ import annotations

from typing import Any

from fastapi import FastAPI


def register_watchdog_routes(app: FastAPI, watchdog: Any) -> None:
    @app.get("/watchdog")
    async def watchdog_status() -> dict[str, Any]:
        return watchdog.snapshot()

    @app.get("/alerts")
    async def alerts() -> dict[str, Any]:
        snapshot = watchdog.snapshot()
        return {"status": snapshot.get("status", "unknown"), "alerts": snapshot.get("alerts", [])}
