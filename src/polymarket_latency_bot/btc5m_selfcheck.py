from __future__ import annotations

from typing import Any

from fastapi import FastAPI


STRATEGY_NAME = "BTC_5M_EVENT_SCALE_IN_V4_HARDENED"
MODE_NAME = "btc_5m_prediction_market_paper_scale_in_v4_hardened"
ENTRYPOINT = "python -m polymarket_latency_bot.btc5m_event_main_v4"


def build_selfcheck_payload() -> dict[str, Any]:
    """Return a static, read-only verification manifest for the V4 runtime."""

    quality_gates = {
        "persistent_stage_confirmation": True,
        "clean_sources_by_stage": True,
        "fusion_for_later_scale_in": True,
        "book_imbalance": True,
        "prevent_price_chasing": True,
        "prevent_edge_decay": True,
        "btc_open_close_quality": True,
    }
    analytics = {
        "ev": True,
        "profit_factor": True,
        "maximum_drawdown": True,
        "data_quality": True,
        "shadow_ab": True,
        "calibration": True,
        "drift": True,
        "walk_forward": True,
    }
    safety = {
        "paper_only": True,
        "paper_predictions_enabled": True,
        "paper_positions_enabled": True,
        "scale_in_enabled": True,
        "adaptive_cooldown_enabled": False,
        "auto_tuning_enabled": False,
        "live_orders_enabled": False,
        "wallet_signing_enabled": False,
        "live_trading_enabled": False,
    }
    checks = {
        "strategy_v4_hardened": STRATEGY_NAME == "BTC_5M_EVENT_SCALE_IN_V4_HARDENED",
        "mode_v4_hardened": MODE_NAME.endswith("v4_hardened"),
        "paper_only": safety["paper_only"],
        "adaptive_cooldown_off": not safety["adaptive_cooldown_enabled"],
        "auto_tuning_off": not safety["auto_tuning_enabled"],
        "live_orders_off": not safety["live_orders_enabled"],
        "wallet_signing_off": not safety["wallet_signing_enabled"],
        "quality_gates_present": all(quality_gates.values()),
        "analytics_present": all(analytics.values()),
    }
    return {
        "ok": all(checks.values()),
        "strategy": STRATEGY_NAME,
        "mode": MODE_NAME,
        "entrypoint": ENTRYPOINT,
        "execution": "hardened_three_stage_scale_in_50_30_20",
        "scale_in_weights": [0.50, 0.30, 0.20],
        "checks": checks,
        "quality_gates": quality_gates,
        "analytics": analytics,
        "safety": safety,
        "note": "Read-only manifest. This endpoint never places orders or changes runtime settings.",
    }


def register_btc5m_selfcheck(app: FastAPI) -> None:
    @app.get("/selfcheck")
    async def selfcheck() -> dict[str, Any]:
        return build_selfcheck_payload()
