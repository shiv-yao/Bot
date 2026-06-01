from __future__ import annotations

import asyncio
from dataclasses import asdict
from time import perf_counter
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .models import Prediction, now_ms


class HaltIn(BaseModel):
    reason: str = Field(default="manual_kill_switch", min_length=1, max_length=120)


class ExternalPredictionIn(BaseModel):
    probability_up: float = Field(ge=0, le=1)
    confidence: float = Field(default=1.0, ge=0, le=1)
    timestamp_ms: int | None = None


class PaperLoadTestIn(BaseModel):
    operations: int = Field(default=1000, ge=1, le=20000)
    concurrency: int = Field(default=100, ge=1, le=1000)


def _require_secret(expected: str, actual: str) -> None:
    if not expected or actual != expected:
        raise HTTPException(status_code=401, detail="invalid webhook secret")


def register_ops_routes(app: FastAPI, settings: Any, state: Any, feeds: Any, risk: Any, portfolio: Any) -> None:
    @app.get("/latency")
    async def latency() -> dict[str, Any]:
        snapshot = await state.snapshot()
        return {"latency": snapshot.get("latency", {}), "runtime_counters": snapshot.get("runtime_counters", {})}

    @app.get("/performance")
    async def performance() -> dict[str, Any]:
        return {"paper": portfolio.store.performance(), "db_path": portfolio.store.db_path}

    @app.post("/risk/halt")
    async def halt(body: HaltIn, x_webhook_secret: str = Header(default="")) -> dict[str, Any]:
        _require_secret(settings.webhook_secret, x_webhook_secret)
        return asdict(await risk.halt(body.reason))

    @app.post("/risk/resume")
    async def resume(x_webhook_secret: str = Header(default="")) -> dict[str, Any]:
        _require_secret(settings.webhook_secret, x_webhook_secret)
        return asdict(await risk.resume())

    @app.post("/feeds/tradingview")
    async def tradingview(body: ExternalPredictionIn, x_webhook_secret: str = Header(default="")) -> dict[str, Any]:
        _require_secret(settings.webhook_secret, x_webhook_secret)
        await feeds.upsert_prediction(Prediction(source="tradingview", probability_up=body.probability_up, confidence=body.confidence, timestamp_ms=body.timestamp_ms or now_ms()))
        return {"accepted": True, "source": "tradingview"}

    @app.post("/feeds/cryptoquant")
    async def cryptoquant(body: ExternalPredictionIn, x_webhook_secret: str = Header(default="")) -> dict[str, Any]:
        _require_secret(settings.webhook_secret, x_webhook_secret)
        await feeds.upsert_prediction(Prediction(source="cryptoquant", probability_up=body.probability_up, confidence=body.confidence, timestamp_ms=body.timestamp_ms or now_ms()))
        return {"accepted": True, "source": "cryptoquant"}

    @app.post("/loadtest/paper")
    async def paper_loadtest(body: PaperLoadTestIn, x_webhook_secret: str = Header(default="")) -> dict[str, Any]:
        _require_secret(settings.webhook_secret, x_webhook_secret)
        semaphore = asyncio.Semaphore(body.concurrency)
        started = perf_counter()

        async def one() -> None:
            async with semaphore:
                await asyncio.sleep(0)

        await asyncio.gather(*(one() for _ in range(body.operations)))
        elapsed = max(0.000001, perf_counter() - started)
        result = {
            "mode": "dry_run",
            "operations": body.operations,
            "concurrency": body.concurrency,
            "elapsed_sec": round(elapsed, 6),
            "ops_per_sec": round(body.operations / elapsed, 2),
            "creates_orders": False,
        }
        await state.increment_counter("paper_loadtest_runs")
        return result
