from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

import uvicorn
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from .btc5m_adaptive_engine import BTC5mAdaptiveRoundPredictionEngine
from .btc5m_performance import build_paper_analytics
from .btc5m_prediction_market_ui import register_btc5m_prediction_market_ui
from .btc5m_safe_fusion import BTC5mSafeFusion
from .config import Settings
from .measured_feeds import MeasuredFeedHub
from .models import Prediction, now_ms
from .multi_source import binance_ws_loop, coinbase_ws_loop
from .rtds_chainlink import chainlink_rtds_loop
from .runtime_profile import apply_balanced_btc5m_paper_profile
from .state import BotState


STRATEGY_NAME = "BTC_5M_EVENT_SCALE_IN_V3_ADAPTIVE_GUARDED"
MODE_NAME = "btc_5m_prediction_market_paper_scale_in_adaptive_guarded"


class ForecastIn(BaseModel):
    probability_up: float = Field(ge=0, le=1)
    confidence: float = Field(default=0.7, ge=0, le=1)


def _secret_ready(value: str) -> bool:
    return bool(value and value != "change-me" and len(value) >= 16)


def _first_not_none(*values: Any, default: float) -> float:
    for value in values:
        if value is not None:
            return float(value)
    return float(default)


def _preview_direction(
    *,
    probability_up: float,
    confidence: float,
    yes_ask: float | None,
    no_ask: float | None,
    min_confidence: float,
    min_edge: float,
) -> tuple[str, str]:
    if confidence < min_confidence:
        return "WAIT", "confidence_too_low"
    if yes_ask is None or no_ask is None:
        return "WAIT", "waiting_for_order_book"
    yes_edge = probability_up - float(yes_ask)
    no_edge = (1 - probability_up) - float(no_ask)
    selected_edge = max(yes_edge, no_edge)
    if selected_edge < min_edge:
        return "WAIT", "edge_too_low"
    return ("YES", "preview_yes_edge") if yes_edge >= no_edge else ("NO", "preview_no_edge")


def _fusion_health(payload: dict[str, Any], required_sources: int) -> dict[str, Any]:
    market_ready = payload.get("market", {}).get("discovery_status") == "ready"
    sources = payload.get("sources", {}) or {}
    connected_sources = sum(1 for source in sources.values() if source.get("connected"))
    fusion = payload.get("fusion", {}) or {}
    fusion_status = str(fusion.get("status") or "waiting_for_sources")
    clean_sources = int(fusion.get("clean_source_count", fusion.get("source_count", 0)) or 0)
    outlier_count = int(fusion.get("outlier_count", 0) or 0)
    dispersion_bps = float(fusion.get("dispersion_bps", 0.0) or 0.0)
    fusion_ready = fusion_status == "ready" and clean_sources >= required_sources
    return {
        "ok": market_ready and connected_sources >= required_sources and fusion_ready,
        "market_ready": market_ready,
        "connected_sources": connected_sources,
        "clean_sources": clean_sources,
        "required_sources": required_sources,
        "fusion_ready": fusion_ready,
        "fusion_status": fusion_status,
        "outlier_count": outlier_count,
        "dispersion_bps": round(dispersion_bps, 6),
    }


def build_mode_status() -> dict[str, Any]:
    return {
        "mode": MODE_NAME,
        "strategy": STRATEGY_NAME,
        "execution": "adaptive_guarded_three_stage_scale_in_50_30_20",
        "market": {"asset": "BTC", "interval_minutes": 5},
        "outputs": ["YES", "NO", "WAIT"],
        "rules": {
            "YES": "BTC close price is higher than BTC open price after 5 minutes",
            "NO": "BTC close price is lower than BTC open price after 5 minutes",
            "WAIT": "A quality gate or adaptive cooldown rejected the Paper entry",
            "max_entries_per_market": 3,
            "scale_in_weights": [0.50, 0.30, 0.20],
            "require_same_direction_revalidation": True,
            "require_fresh_signal": True,
            "require_fresh_book": True,
            "require_net_edge": True,
            "require_book_depth": True,
            "adaptive_cooldown": True,
            "auto_tuning_enabled": False,
            "settlement": "btc_close_vs_btc_open",
        },
        "safety": {
            "paper_predictions_enabled": True,
            "paper_orders_enabled": True,
            "paper_positions_enabled": True,
            "scale_in_enabled": True,
            "adaptive_cooldown_enabled": True,
            "paper_only": True,
            "live_orders_enabled": False,
            "wallet_signing_enabled": False,
            "live_trading_enabled": False,
            "general_event_scanner_enabled": False,
            "hft_repeated_orders_enabled": False,
        },
    }


async def build_status(settings: Settings, state: BotState) -> dict[str, Any]:
    snapshot = await state.snapshot()
    predictions = snapshot.get("predictions", {}) or {}
    fusion = snapshot.get("fusion_snapshot", {}) or {}
    selected = predictions.get("multi_source_fusion") or predictions.get("rtds_momentum_fallback") or fusion
    probability_up = _first_not_none(selected.get("probability_up"), fusion.get("probability_up"), default=0.5)
    confidence = _first_not_none(selected.get("confidence"), fusion.get("confidence"), default=0.0)

    market = snapshot.get("current_market") or {}
    yes_token_id = str(market.get("yes_token_id") or getattr(settings, "yes_token_id", "") or "")
    no_token_id = str(market.get("no_token_id") or getattr(settings, "no_token_id", "") or "")
    books = snapshot.get("books", {}) or {}
    yes_book = books.get(yes_token_id, {}) or {}
    no_book = books.get(no_token_id, {}) or {}
    yes_ask = yes_book.get("best_ask")
    no_ask = no_book.get("best_ask")
    yes_edge = probability_up - float(yes_ask) if yes_ask is not None else 0.0
    no_edge = (1 - probability_up) - float(no_ask) if no_ask is not None else 0.0

    min_confidence = float(getattr(settings, "min_confidence", 0.56))
    min_edge = float(getattr(settings, "min_edge", 0.02))
    min_probability_margin = float(getattr(settings, "ai_min_probability_margin", 0.006))
    preview_direction, preview_reason = _preview_direction(
        probability_up=probability_up,
        confidence=confidence,
        yes_ask=yes_ask,
        no_ask=no_ask,
        min_confidence=min_confidence,
        min_edge=min_edge,
    )

    paper = snapshot.get("paper_portfolio", {}) or {}
    current_round = paper.get("current_round") or {}
    round_direction = str(current_round.get("direction") or "")
    direction = round_direction if round_direction in {"YES", "NO"} else preview_direction
    reason = current_round.get("reason") or paper.get("last_reason") or preview_reason
    return {
        **build_mode_status(),
        "market": {
            "asset": "BTC",
            "interval_minutes": 5,
            "discovery_status": snapshot.get("market_discovery_status"),
            "current": market,
            "yes_token_id": yes_token_id,
            "no_token_id": no_token_id,
            "yes_ask": yes_ask,
            "no_ask": no_ask,
            "yes_book_age_ms": max(0, now_ms() - int(yes_book.get("timestamp_ms") or 0)) if yes_book else None,
            "no_book_age_ms": max(0, now_ms() - int(no_book.get("timestamp_ms") or 0)) if no_book else None,
        },
        "ai": {
            "direction": direction,
            "reason": reason,
            "preview_direction": preview_direction,
            "preview_reason": preview_reason,
            "probability_up": probability_up,
            "confidence": confidence,
            "yes_edge": round(yes_edge, 6),
            "no_edge": round(no_edge, 6),
            "selected_edge": round(max(yes_edge, no_edge), 6),
            "last_signal_quality": current_round.get("last_signal_quality") or {},
            "min_confidence": min_confidence,
            "min_edge": min_edge,
            "min_probability_margin": min_probability_margin,
        },
        "paper": paper,
        "execution_metrics": {
            "predictions_submitted": snapshot.get("orders_submitted", 0),
            "predictions_rejected": snapshot.get("orders_rejected", 0),
            "queue_depth": 0,
            "last_prediction_result": snapshot.get("last_order_result"),
        },
        "sources": snapshot.get("source_status", {}),
        "fusion": fusion,
        "connections": snapshot.get("connections", {}),
        "last_error": snapshot.get("last_error"),
    }


async def run() -> None:
    settings = Settings()
    apply_balanced_btc5m_paper_profile(settings)
    state = BotState()

    round_engine: BTC5mAdaptiveRoundPredictionEngine | None = None

    async def evaluate() -> None:
        await state.record_event("prediction_evaluation")
        if round_engine is not None:
            await round_engine.evaluate()

    feeds = MeasuredFeedHub(settings, state, evaluate)
    fusion = BTC5mSafeFusion(settings, state, feeds)
    round_engine = BTC5mAdaptiveRoundPredictionEngine(settings, state)
    await round_engine.publish_state()

    app = FastAPI(title="Polymarket BTC 5m Prediction Market Paper Scale In Adaptive Guarded")
    register_btc5m_prediction_market_ui(app)

    @app.get("/", include_in_schema=False)
    async def dashboard() -> RedirectResponse:
        return RedirectResponse(url="/ui", status_code=307)

    @app.get("/mode")
    async def mode() -> dict[str, Any]:
        return build_mode_status()

    @app.get("/status")
    async def status() -> dict[str, Any]:
        return await build_status(settings, state)

    @app.get("/paper/status")
    async def paper_status() -> dict[str, Any]:
        snapshot = await state.snapshot()
        return {
            "mode": MODE_NAME,
            "strategy": STRATEGY_NAME,
            "portfolio": snapshot.get("paper_portfolio", {}),
            "predictions_submitted": snapshot.get("orders_submitted", 0),
        }

    @app.get("/paper/winrate")
    async def paper_winrate() -> dict[str, Any]:
        snapshot = await state.snapshot()
        summary = (snapshot.get("paper_portfolio", {}) or {}).get("summary", {}) or {}
        closed_trades = int(summary.get("closed_trades", 0) or 0)
        return {
            "mode": MODE_NAME,
            "strategy": STRATEGY_NAME,
            "win_rate": float(summary.get("win_rate", 0.0) or 0.0),
            "wins": int(summary.get("wins", 0) or 0),
            "losses": int(summary.get("losses", 0) or 0),
            "flat": int(summary.get("flat", 0) or 0),
            "closed_rounds": closed_trades,
            "open_rounds": int(summary.get("open_positions", 0) or 0),
            "skipped_wait": int(summary.get("skipped_wait", 0) or 0),
            "rejection_counts": summary.get("rejection_counts", {}),
            "sample_status": "collecting" if closed_trades < 100 else "review_ready",
        }

    @app.get("/paper/analytics")
    async def paper_analytics() -> dict[str, Any]:
        snapshot = await state.snapshot()
        paper = snapshot.get("paper_portfolio", {}) or {}
        analytics = build_paper_analytics(
            paper,
            cooldown_after_losses=round_engine.cooldown_after_losses,
            min_samples_for_review=round_engine.analytics_min_samples,
            min_group_samples_for_review=round_engine.review_min_group_samples,
            review_win_rate_threshold=round_engine.review_win_rate_threshold,
            rolling_window=round_engine.rolling_window,
            calibration_min_bucket_samples=round_engine.calibration_min_bucket_samples,
            overconfidence_gap_threshold=round_engine.overconfidence_gap_threshold,
            drift_min_samples=round_engine.drift_min_samples,
            drift_win_rate_drop_threshold=round_engine.drift_win_rate_drop_threshold,
            drift_brier_increase_threshold=round_engine.drift_brier_increase_threshold,
            walk_forward_train_min_samples=round_engine.walk_forward_train_min_samples,
            walk_forward_validation_samples=round_engine.walk_forward_validation_samples,
            walk_forward_win_rate_drop_threshold=round_engine.walk_forward_win_rate_drop_threshold,
            walk_forward_brier_increase_threshold=round_engine.walk_forward_brier_increase_threshold,
        )
        return {
            "mode": MODE_NAME,
            "strategy": STRATEGY_NAME,
            "analytics": analytics,
            "adaptive_guard": paper.get("adaptive_guard", {}),
        }

    @app.get("/paper/rounds")
    async def paper_rounds() -> dict[str, Any]:
        snapshot = await state.snapshot()
        paper = snapshot.get("paper_portfolio", {}) or {}
        return {
            "mode": MODE_NAME,
            "strategy": STRATEGY_NAME,
            "current_round": paper.get("current_round"),
            "closed_rounds": paper.get("closed_trades", []),
            "skipped_rounds": paper.get("skipped_rounds", []),
            "rules": paper.get("rules", {}),
        }

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        payload = await build_status(settings, state)
        required_sources = int(getattr(settings, "fusion_min_sources", 2))
        return {"mode": payload["mode"], **_fusion_health(payload, required_sources)}

    async def upsert_external(source: str, body: ForecastIn, secret: str) -> dict[str, Any]:
        if not _secret_ready(settings.webhook_secret):
            raise HTTPException(status_code=503, detail="WEBHOOK_SECRET is not configured")
        if secret != settings.webhook_secret:
            raise HTTPException(status_code=401, detail="invalid webhook secret")
        prediction = Prediction(
            source=source,
            probability_up=body.probability_up,
            confidence=body.confidence,
            timestamp_ms=now_ms(),
        )
        await feeds.upsert_prediction(prediction)
        return {"accepted": True, "source": source, "prediction": prediction.to_dict()}

    @app.post("/feeds/tradingview")
    async def tradingview(body: ForecastIn, x_webhook_secret: str = Header(default="")) -> dict[str, Any]:
        return await upsert_external("tradingview", body, x_webhook_secret)

    @app.post("/feeds/cryptoquant")
    async def cryptoquant(body: ForecastIn, x_webhook_secret: str = Header(default="")) -> dict[str, Any]:
        return await upsert_external("cryptoquant", body, x_webhook_secret)

    tasks: list[asyncio.Task[object]] = [
        asyncio.create_task(feeds.market_discovery_loop(), name="market-discovery"),
        asyncio.create_task(feeds.market_ws_loop(), name="market-ws"),
        asyncio.create_task(chainlink_rtds_loop(settings, state, feeds, fusion), name="chainlink-rtds"),
        asyncio.create_task(binance_ws_loop(settings, state, fusion), name="binance-ws"),
        asyncio.create_task(coinbase_ws_loop(settings, state, fusion), name="coinbase-ws"),
        asyncio.create_task(feeds.external_poll_loop(), name="external-poll"),
        asyncio.create_task(round_engine.loop(), name="btc5m-round-prediction"),
    ]
    server = uvicorn.Server(uvicorn.Config(app, host=settings.host, port=settings.port, log_level="warning"))
    tasks.append(asyncio.create_task(server.serve(), name="api"))
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
