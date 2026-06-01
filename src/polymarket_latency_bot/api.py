from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .config import Settings
from .feeds import FeedHub
from .models import Prediction, now_ms
from .risk import RiskManager
from .state import BotState


class PredictionIn(BaseModel):
    source: str = Field(min_length=1, max_length=64)
    probability_up: float = Field(ge=0, le=1)
    confidence: float = Field(default=1.0, ge=0, le=1)
    timestamp_ms: int | None = None


class PnlAdjustmentIn(BaseModel):
    delta_usd: float


def create_app(settings: Settings, state: BotState, feeds: FeedHub, risk: RiskManager) -> FastAPI:
    app = FastAPI(title="Polymarket Latency Bot", version="0.1.0")

    @app.get("/")
    async def root() -> dict[str, Any]:
        return {"service": "polymarket-latency-bot", "mode": "live" if settings.live_enabled else "paper"}

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "ok": True,
            "mode": "live" if settings.live_enabled else "paper",
            "market_ws_required": bool(settings.yes_token_id and settings.no_token_id),
        }

    @app.get("/state")
    async def state_snapshot() -> dict[str, Any]:
        return await state.snapshot()

    @app.get("/risk")
    async def risk_snapshot() -> dict[str, Any]:
        async with risk.lock:
            return asdict(risk.snapshot)

    @app.post("/feeds/prediction")
    async def prediction_webhook(
        body: PredictionIn,
        x_webhook_secret: str = Header(default=""),
    ) -> dict[str, Any]:
        if x_webhook_secret != settings.webhook_secret:
            raise HTTPException(status_code=401, detail="invalid webhook secret")
        await feeds.upsert_prediction(Prediction(
            source=body.source,
            probability_up=body.probability_up,
            confidence=body.confidence,
            timestamp_ms=body.timestamp_ms or now_ms(),
        ))
        return {"accepted": True}

    @app.post("/risk/pnl-adjustment")
    async def pnl_adjustment(
        body: PnlAdjustmentIn,
        x_webhook_secret: str = Header(default=""),
    ) -> dict[str, Any]:
        if x_webhook_secret != settings.webhook_secret:
            raise HTTPException(status_code=401, detail="invalid webhook secret")
        snapshot = await risk.manual_pnl_adjustment(body.delta_usd)
        return asdict(snapshot)

    return app
