from __future__ import annotations

from typing import Any

from fastapi import FastAPI


def register_ai_routes(app: FastAPI, settings: Any, state: Any) -> None:
    @app.get("/ai/status")
    async def ai_status() -> dict[str, Any]:
        snapshot = await state.snapshot()
        strategy = snapshot.get("last_strategy_snapshot", {}) or {}
        direction = strategy.get("direction")
        if not direction:
            direction = "WAIT"
        return {
            "mode": "paper",
            "market": {
                "asset": "BTC",
                "interval_sec": int(settings.market_interval_sec),
                "interval_minutes": round(float(settings.market_interval_sec) / 60, 2),
                "slug_prefix": str(settings.market_slug_prefix),
                "discovery_status": snapshot.get("market_discovery_status"),
                "current_market": snapshot.get("current_market"),
            },
            "ai": {
                "decision_mode": strategy.get("ai_mode", "single_direction_yes_no"),
                "direction": direction,
                "decision": strategy.get("decision", "waiting"),
                "fair_probability_up": strategy.get("fair_probability_up"),
                "selected_probability": strategy.get("fair_probability"),
                "confidence": strategy.get("confidence"),
                "probability_margin": strategy.get("probability_margin", settings.ai_min_probability_margin),
                "reason": strategy.get("reason"),
                "timestamp_ms": strategy.get("timestamp_ms"),
            },
            "fusion": snapshot.get("fusion_snapshot", {}),
            "connections": snapshot.get("connections", {}),
        }
