from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Any

import uvicorn
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from .btc5m_event_paper_ui import register_btc5m_event_paper_ui
from .config import Settings
from .measured_feeds import MeasuredFeedHub
from .models import Prediction, now_ms
from .multi_source import MultiSourceFusion, binance_ws_loop, coinbase_ws_loop
from .paper_metrics import MeasuredPaperExecutor
from .paper_portfolio import PaperPortfolio
from .risk import RiskManager
from .rtds_chainlink import chainlink_rtds_loop
from .runtime_profile import apply_balanced_btc5m_paper_profile
from .state import BotState
from .strategy import LatencyStrategy


class ForecastIn(BaseModel):
    probability_up: float = Field(ge=0, le=1)
    confidence: float = Field(default=0.7, ge=0, le=1)


def _secret_ready(value: str) -> bool:
    return bool(value and value != "change-me" and len(value) >= 16)


def build_mode_status() -> dict[str, Any]:
    return {
        "mode": "btc_5m_event_prediction_paper",
        "execution": "paper_simulation",
        "market": {"asset": "BTC", "interval_minutes": 5},
        "outputs": ["YES", "NO", "WAIT"],
        "safety": {
            "paper_orders_enabled": True,
            "paper_positions_enabled": True,
            "live_orders_enabled": False,
            "general_event_scanner_enabled": False,
            "wallet_signing_enabled": False,
            "live_trading_enabled": False,
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
    selected_edge = max(yes_edge, no_edge)
    direction = "WAIT"
    if confidence >= settings.min_confidence and selected_edge >= settings.min_edge:
        direction = "YES" if yes_edge >= no_edge else "NO"
    paper = snapshot.get("paper_portfolio", {}) or {}
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
            "direction": direction,
            "probability_up": probability_up,
            "confidence": confidence,
            "yes_edge": round(yes_edge, 6),
            "no_edge": round(no_edge, 6),
            "selected_edge": round(selected_edge, 6),
            "min_edge": settings.min_edge,
            "min_confidence": settings.min_confidence,
        },
        "paper": paper,
        "execution_metrics": {
            "orders_submitted": snapshot.get("orders_submitted", 0),
            "orders_rejected": snapshot.get("orders_rejected", 0),
            "queue_depth": snapshot.get("queue_depth", 0),
            "queue_high_water": snapshot.get("queue_high_water", 0),
            "last_order_result": snapshot.get("last_order_result"),
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
    risk = RiskManager(settings)
    portfolio = PaperPortfolio(settings, state, risk, logging.getLogger("btc5m_event_paper"))
    strategy = LatencyStrategy(settings, state)
    executor = MeasuredPaperExecutor(settings, state, risk, portfolio)

    async def evaluate() -> None:
        await state.record_event("prediction_evaluation")
        intents = await strategy.build_intents()
        if intents:
            await state.increment_counter("paper_strategy_intents", len(intents))
        for intent in intents:
            await executor.submit(intent)

    feeds = MeasuredFeedHub(settings, state, evaluate)
    fusion = MultiSourceFusion(settings, state, feeds)
    app = FastAPI(title="BTC 5m Event Prediction Paper")
    register_btc5m_event_paper_ui(app)

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
            "mode": "paper_simulation",
            "portfolio": snapshot.get("paper_portfolio", {}),
            "orders_submitted": snapshot.get("orders_submitted", 0),
            "orders_rejected": snapshot.get("orders_rejected", 0),
            "queue_depth": snapshot.get("queue_depth", 0),
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
        asyncio.create_task(portfolio.mark_loop(), name="paper-portfolio-mark"),
    ]
    tasks += [
        asyncio.create_task(executor.worker(worker_id), name=f"paper-executor-{worker_id}")
        for worker_id in range(settings.execution_workers)
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
