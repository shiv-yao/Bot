from __future__ import annotations

import asyncio
from dataclasses import asdict
from time import perf_counter
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from .models import Prediction, now_ms
from .report_api import register_report_routes


class HaltIn(BaseModel):
    reason: str = Field(default="manual_kill_switch", min_length=1, max_length=120)


class ExternalPredictionIn(BaseModel):
    probability_up: float = Field(ge=0, le=1)
    confidence: float = Field(default=1.0, ge=0, le=1)
    timestamp_ms: int | None = None


class PaperLoadTestIn(BaseModel):
    operations: int = Field(default=1000, ge=1, le=20000)
    concurrency: int = Field(default=100, ge=1, le=1000)


OPS_HTML = """
<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Paper Ops</title></head><body style="background:#07111f;color:#eef5ff;font-family:system-ui;padding:16px"><h1>Polymarket Paper Ops</h1><p>延遲、績效、報表與風控</p><p><a href="/monitor">Monitor</a> · <a href="/latency">Latency</a> · <a href="/performance">Performance</a> · <a href="/report/daily">Daily</a> · <a href="/report/exit-reasons">Exit Reasons</a> · <a href="/export/trades.csv">CSV</a> · <a href="/security/status">Security</a> · <a href="/docs">Docs</a></p><pre id="out">loading...</pre><script>async function refresh(){const [l,p,r,s]=await Promise.all([fetch('/latency').then(x=>x.json()),fetch('/performance').then(x=>x.json()),fetch('/risk').then(x=>x.json()),fetch('/security/status').then(x=>x.json())]);document.getElementById('out').textContent=JSON.stringify({latency:l,performance:p,risk:r,security:s},null,2)}refresh();setInterval(refresh,3000)</script></body></html>
"""


def _secret_ready(value: str) -> bool:
    return bool(value and value != "change-me" and len(value) >= 16)


def _require_secret(expected: str, actual: str) -> None:
    if not _secret_ready(expected):
        raise HTTPException(status_code=503, detail="WEBHOOK_SECRET is not configured")
    if actual != expected:
        raise HTTPException(status_code=401, detail="invalid webhook secret")


def register_ops_routes(app: FastAPI, settings: Any, state: Any, feeds: Any, risk: Any, portfolio: Any) -> None:
    register_report_routes(app, portfolio)
    protected_paths = {
        "/feeds/prediction", "/risk/pnl-adjustment", "/risk/halt", "/risk/resume",
        "/feeds/tradingview", "/feeds/cryptoquant", "/loadtest/paper", "/loadtest/pipeline",
    }

    @app.middleware("http")
    async def protect_default_secret(request: Request, call_next: Any) -> Any:
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path in protected_paths:
            if not _secret_ready(settings.webhook_secret):
                return JSONResponse(status_code=503, content={"detail": "WEBHOOK_SECRET is not configured"})
        return await call_next(request)

    @app.get("/ops", response_class=HTMLResponse)
    async def ops() -> HTMLResponse:
        return HTMLResponse(OPS_HTML)

    @app.get("/latency")
    async def latency() -> dict[str, Any]:
        snapshot = await state.snapshot()
        return {"latency": snapshot.get("latency", {}), "runtime_counters": snapshot.get("runtime_counters", {})}

    @app.get("/performance")
    async def performance() -> dict[str, Any]:
        return {"paper": portfolio.store.performance(), "db_path": portfolio.store.db_path}

    @app.get("/risk/status")
    async def risk_status() -> dict[str, Any]:
        async with risk.lock:
            return asdict(risk.snapshot)

    @app.get("/security/status")
    async def security_status() -> dict[str, Any]:
        return {"mode": "paper", "live_enabled": bool(settings.live_enabled), "webhook_secret_configured": _secret_ready(settings.webhook_secret), "paper_db_path": portfolio.store.db_path}

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

    async def _run_dry_operations(body: PaperLoadTestIn, pipeline: bool) -> dict[str, Any]:
        semaphore = asyncio.Semaphore(body.concurrency)
        started = perf_counter()

        async def one() -> None:
            async with semaphore:
                stage_started = perf_counter()
                await asyncio.sleep(0)
                if pipeline:
                    await asyncio.sleep(0)
                    await asyncio.sleep(0)
                await state.record_latency("pipeline_dry_run_ms" if pipeline else "task_dry_run_ms", (perf_counter() - stage_started) * 1000)

        await asyncio.gather(*(one() for _ in range(body.operations)))
        elapsed = max(0.000001, perf_counter() - started)
        await state.increment_counter("pipeline_loadtest_runs" if pipeline else "paper_loadtest_runs")
        return {"mode": "pipeline_dry_run" if pipeline else "task_dry_run", "operations": body.operations, "concurrency": body.concurrency, "elapsed_sec": round(elapsed, 6), "ops_per_sec": round(body.operations / elapsed, 2), "creates_orders": False, "changes_positions": False}

    @app.post("/loadtest/paper")
    async def paper_loadtest(body: PaperLoadTestIn, x_webhook_secret: str = Header(default="")) -> dict[str, Any]:
        _require_secret(settings.webhook_secret, x_webhook_secret)
        return await _run_dry_operations(body, False)

    @app.post("/loadtest/pipeline")
    async def pipeline_loadtest(body: PaperLoadTestIn, x_webhook_secret: str = Header(default="")) -> dict[str, Any]:
        _require_secret(settings.webhook_secret, x_webhook_secret)
        return await _run_dry_operations(body, True)
