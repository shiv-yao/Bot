from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import asdict
from typing import Any

from .diagnostics import build_diagnostics
from .models import now_ms


class RuntimeWatchdog:
    def __init__(self, settings: Any, state: Any, risk: Any, portfolio: Any) -> None:
        self.settings = settings
        self.state = state
        self.risk = risk
        self.portfolio = portfolio
        self.alerts: deque[dict[str, Any]] = deque(maxlen=200)
        self.last_status = "unknown"
        self.last_snapshot: dict[str, Any] = {}

    async def check_once(self) -> dict[str, Any]:
        snapshot = await self.state.snapshot()
        async with self.risk.lock:
            risk_snapshot = self.risk.snapshot
        diagnostics = build_diagnostics(self.settings, snapshot, risk_snapshot, self.portfolio.store.db_path)
        status = str(diagnostics.get("status") or "unknown")
        if status != self.last_status:
            self.alerts.appendleft({
                "timestamp_ms": now_ms(),
                "event": "watchdog_status_changed",
                "previous_status": self.last_status,
                "status": status,
                "warnings": diagnostics.get("warnings", []),
            })
            self.last_status = status
        self.last_snapshot = diagnostics
        return diagnostics

    async def loop(self) -> None:
        while True:
            try:
                await self.check_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.alerts.appendleft({
                    "timestamp_ms": now_ms(),
                    "event": "watchdog_error",
                    "error": str(exc),
                })
            await asyncio.sleep(10)

    def snapshot(self) -> dict[str, Any]:
        return {
            "status": self.last_status,
            "last_snapshot": self.last_snapshot,
            "alerts": list(self.alerts),
            "risk": asdict(self.risk.snapshot),
        }
