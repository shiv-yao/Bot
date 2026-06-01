from __future__ import annotations

import asyncio
from dataclasses import asdict
from time import perf_counter
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
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


OPS_HTML = r"""
<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Bot Ops</title><style>
body{margin:0;background:#07111f;color:#eef5ff;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.wrap{max-width:980px;margin:auto;padding:14px}.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:12px}.card{grid-column:span 12;background:#10213a;border:1px solid #2a405e;border-radius:18px;padding:14px}.half{grid-column:span 6}.row{display:flex;justify-content:space-between;gap:12px;padding:8px 0;border-bottom:1px solid rgba(159,178,204,.18)}.row:last-child{border:0}.muted{color:#9fb2cc}.pill{display:inline-block;padding:7px 10px;border:1px solid #2a405e;border-radius:99px;font-weight:800}.ok{color:#b7f7ca}.bad{color:#fecaca}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;overflow-wrap:anywhere}a{color:#b9e6ff}@media(max-width:760px){.half{grid-column:span 12}}
</style></head><body><main class="wrap"><h1>Polymarket Paper Ops</h1><p class="muted">延遲、績效、風控與來源健康</p><section class="grid"><article class="card half"><h2>風控</h2><div id="risk"></div></article><article class="card half"><h2>安全狀態</h2><div id="security"></div></article><article class="card"><h2>延遲分位數</h2><div id="latency"></div></article><article class="card"><h2>Paper 績效</h2><div id="performance"></div></article><article class="card"><h2>資料來源</h2><div id="sources"></div></article><article class="card"><h2>API</h2><div class="mono"><a href="/monitor">/monitor</a> · <a href="/latency">/latency</a> · <a href="/performance">/performance</a> · <a href="/risk">/risk</a> · <a href="/security/status">/security/status</a> · <a href="/docs">/docs</a></div></article></section></main><script>
const row=(a,b)=>`<div class="row"><span>${a}</span><strong>${b}</strong></div>`;const n=(x,d=4)=>Number.isFinite(Number(x))?Number(x).toFixed(d):'—';async function refresh(){const [lr,pr,rr,sr,st]=await Promise.all([fetch('/latency'),fetch('/performance'),fetch('/risk'),fetch('/security/status'),fetch('/debug/sources')]);const l=await lr.json(),p=(await pr.json()).paper||{},r=await rr.json(),s=await sr.json(),src=(await st.json()).source_status||{};document.getElementById('risk').innerHTML=row('狀態',r.halted?`HALTED: ${r.halt_reason}`:'RUNNING')+row('已實現 PnL',n(r.realized_pnl))+row('目前曝險',n(r.open_notional));document.getElementById('security').innerHTML=row('模式',s.mode)+row('Live',s.live_enabled?'ON':'OFF')+row('Webhook Secret',s.webhook_secret_configured?'已設定':'仍為預設值')+row('Volume',s.paper_db_path);document.getElementById('latency').innerHTML=Object.entries(l.latency||{}).map(([k,v])=>row(k,`p50 ${n(v.p50_ms)} · p95 ${n(v.p95_ms)} · p99 ${n(v.p99_ms)} ms`)).join('');document.getElementById('performance').innerHTML=row('平倉數',p.closed_trades??0)+row('Profit Factor',p.profit_factor??'—')+row('最大回撤',n(p.max_drawdown))+row('平均單筆',n(p.average_trade_pnl))+row('平均持有',`${n(p.average_hold_ms,2)} ms`)+row('勝率',`${n((p.win_rate||0)*100,2)}%`);document.getElementById('sources').innerHTML=Object.entries(src).map(([k,v])=>row(k,`${v.connected?'ON':'OFF'} · ${v.last_error||'ok'}`)).join('')}refresh();setInterval(refresh,3000);
</script></body></html>
"""


def _secret_ready(value: str) -> bool:
    return bool(value and value != "change-me" and len(value) >= 16)


def _require_secret(expected: str, actual: str) -> None:
    if not _secret_ready(expected):
        raise HTTPException(status_code=503, detail="WEBHOOK_SECRET is not configured")
    if actual != expected:
        raise HTTPException(status_code=401, detail="invalid webhook secret")


def register_ops_routes(app: FastAPI, settings: Any, state: Any, feeds: Any, risk: Any, portfolio: Any) -> None:
    protected_paths = {
        "/feeds/prediction", "/risk/pnl-adjustment", "/risk/halt", "/risk/resume",
        "/feeds/tradingview", "/feeds/cryptoquant", "/loadtest/paper",
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
        return {
            "mode": "paper",
            "live_enabled": bool(settings.live_enabled),
            "webhook_secret_configured": _secret_ready(settings.webhook_secret),
            "paper_db_path": portfolio.store.db_path,
        }

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
