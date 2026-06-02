from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

import uvicorn
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from .btc5m_prediction_market_ui import register_btc5m_prediction_market_ui
from .btc5m_round_prediction import BTC5mRoundPredictionEngine
from .config import Settings
from .measured_feeds import MeasuredFeedHub
from .models import Prediction, now_ms
from .multi_source import MultiSourceFusion, binance_ws_loop, coinbase_ws_loop
from .rtds_chainlink import chainlink_rtds_loop
from .runtime_profile import apply_balanced_btc5m_paper_profile
from .state import BotState


class ForecastIn(BaseModel):
    probability_up: float = Field(ge=0, le=1)
    confidence: float = Field(default=0.7, ge=0, le=1)


def _secret_ready(value: str) -> bool:
    return bool(value and value != "change-me" and len(value) >= 16)


def build_mode_status() -> dict[str, Any]:
    return {
        "mode": "btc_5m_prediction_market_paper_scale_in",
        "strategy": "BTC_5M_EVENT_SCALE_IN_V1",
        "execution": "three_stage_scale_in_50_30_20",
        "market": {"asset": "BTC", "interval_minutes": 5},
        "outputs": ["YES", "NO", "WAIT"],
        "rules": {
            "YES": "BTC close price is higher than BTC open price after 5 minutes",
            "NO": "BTC close price is lower than BTC open price after 5 minutes",
            "WAIT": "AI confidence or direction margin is insufficient; no Paper entry is created",
            "max_entries_per_market": 3,
            "scale_in_weights": [0.50, 0.30, 0.20],
            "require_same_direction_revalidation": True,
            "settlement": "btc_close_vs_btc_open",
        },
        "safety": {
            "paper_predictions_enabled": True,
            "scale_in_enabled": True,
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
    predictions = snapshot.get("predictions", {})
    fusion = snapshot.get("fusion_snapshot", {})
    selected = predictions.get("multi_source_fusion") or predictions.get("rtds_momentum_fallback") or {}
    probability_up = float(selected.get("probability_up") or fusion.get("probability_up") or 0.5)
    confidence = float(selected.get("confidence") or fusion.get("confidence") or 0.0)
    books = snapshot.get("books", {})
    yes_book = books.get(settings.yes_token_id, {})
    no_book = books.get(settings.no_token_id, {})
    yes_ask = yes_book.get("best_ask")
    no_ask = no_book.get("best_ask")
    yes_edge = probability_up - float(yes_ask) if yes_ask is not None else 0.0
    no_edge = (1 - probability_up) - float(no_ask) if no_ask is not None else 0.0
    paper = snapshot.get("paper_portfolio", {}) or {}
    current_round = paper.get("current_round") or {}
    return {
        **build_mode_status(),
        "market": {
            "asset": "BTC",
            "interval_minutes": 5,
            "discovery_status": snapshot.get("market_discovery_status"),
            "current": snapshot.get("current_market"),
            "yes_ask": yes_ask,
            "no_ask": no_ask,
        },
        "ai": {
            "direction": current_round.get("direction") or "WAIT",
            "reason": current_round.get("reason") or paper.get("last_reason") or "starting",
            "probability_up": probability_up,
            "confidence": confidence,
            "yes_edge": round(yes_edge, 6),
            "no_edge": round(no_edge, 6),
            "selected_edge": round(max(yes_edge, no_edge), 6),
            "min_confidence": settings.min_confidence,
            "min_probability_margin": settings.ai_min_probability_margin,
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

    round_engine: BTC5mRoundPredictionEngine | None = None

    async def evaluate() -> None:
        await state.record_event("prediction_evaluation")
        if round_engine is not None:
            await round_engine.evaluate()

    feeds = MeasuredFeedHub(settings, state, evaluate)
    fusion = MultiSourceFusion(settings, state, feeds)
    round_engine = BTC5mRoundPredictionEngine(settings, state)
    await round_engine.publish_state()

    app = FastAPI(title="Polymarket BTC 5m Prediction Market Paper Scale In")
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
            "mode": "btc_5m_prediction_market_paper_scale_in",
            "strategy": "BTC_5M_EVENT_SCALE_IN_V1",
            "portfolio": snapshot.get("paper_portfolio", {}),
            "predictions_submitted": snapshot.get("orders_submitted", 0),
        }

    @app.get("/paper/winrate")
    async def paper_winrate() -> dict[str, Any]:
        snapshot = await state.snapshot()
        summary = (snapshot.get("paper_portfolio", {}) or {}).get("summary", {}) or {}
        closed_trades = int(summary.get("closed_trades", 0) or 0)
        return {
            "mode": "btc_5m_prediction_market_paper_scale_in",
            "strategy": "BTC_5M_EVENT_SCALE_IN_V1",
            "win_rate": float(summary.get("win_rate", 0.0) or 0.0),
            "wins": int(summary.get("wins", 0) or 0),
            "losses": int(summary.get("losses", 0) or 0),
            "flat": int(summary.get("flat", 0) or 0),
            "closed_rounds": closed_trades,
            "open_rounds": int(summary.get("open_positions", 0) or 0),
            "skipped_wait": int(summary.get("skipped_wait", 0) or 0),
            "sample_status": "collecting" if closed_trades < 100 else "review_ready",
        }

    @app.get("/paper/rounds")
    async def paper_rounds() -> dict[str, Any]:
        snapshot = await state.snapshot()
        paper = snapshot.get("paper_portfolio", {}) or {}
        return {
            "mode": "btc_5m_prediction_market_paper_scale_in",
            "strategy": "BTC_5M_EVENT_SCALE_IN_V1",
            "current_round": paper.get("current_round"),
            "closed_rounds": paper.get("closed_trades", []),
            "skipped_rounds": paper.get("skipped_rounds", []),
            "rules": paper.get("rules", {}),
        }

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        payload = await build_status(settings, state)
        return {"ok": payload["market"]["discovery_status"] == "ready", "mode": payload["mode"]}

    async def upsert_external(source: str, body: ForecastIn, secret: str) -> dict[str, Any]:
        if not _secret_ready(settings.webhook_secret):
            raise HTTPException(status_code=503, detail="WEBHOOK_SECRET is not configured")
        if secret != settings.webhook_secret:
            raise HTTPException(status_code=401, detail="invalid webhook secret")
        prediction = Prediction(source=source, probability_up=body.probability_up, confidence=body.confidence, timestamp_ms=now_ms())
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
