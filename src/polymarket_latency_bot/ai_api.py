from __future__ import annotations

from typing import Any

from fastapi import FastAPI


def _risk_profile(settings: Any) -> dict[str, Any]:
    return {
        "paper_high_frequency_profile": bool(settings.paper_high_frequency_profile),
        "live_enabled": bool(settings.live_enabled),
        "effective_max_order_equity_fraction": float(settings.effective_max_order_equity_fraction),
        "effective_max_daily_loss_fraction": float(settings.effective_max_daily_loss_fraction),
        "effective_max_open_notional_usd": float(settings.effective_max_open_notional_usd),
        "signal_cooldown_ms": int(settings.signal_cooldown_ms),
        "strategy_evaluation_interval_ms": int(settings.strategy_evaluation_interval_ms),
        "execution_workers": int(settings.execution_workers),
        "max_queue_size": int(settings.max_queue_size),
        "paper_disable_order_rate_limit": bool(settings.paper_disable_order_rate_limit),
        "paper_hold_sec": int(settings.paper_hold_sec),
        "paper_mark_interval_sec": float(settings.paper_mark_interval_sec),
        "paper_open_buffer_sec": int(settings.paper_open_buffer_sec),
        "paper_close_buffer_sec": int(settings.paper_close_buffer_sec),
        "paper_max_consecutive_losses_per_market": int(settings.paper_max_consecutive_losses_per_market),
    }


def register_ai_routes(app: FastAPI, settings: Any, state: Any) -> None:
    @app.get("/risk/profile")
    async def risk_profile() -> dict[str, Any]:
        return {"mode": "paper" if not settings.live_enabled else "live", "risk_profile": _risk_profile(settings)}

    @app.get("/ai/status")
    async def ai_status() -> dict[str, Any]:
        snapshot = await state.snapshot()
        strategy = snapshot.get("last_strategy_snapshot", {}) or {}
        direction = strategy.get("direction")
        if not direction:
            direction = "WAIT"
        return {
            "mode": "paper" if not settings.live_enabled else "live",
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
            "risk_profile": _risk_profile(settings),
            "fusion": snapshot.get("fusion_snapshot", {}),
            "connections": snapshot.get("connections", {}),
        }
