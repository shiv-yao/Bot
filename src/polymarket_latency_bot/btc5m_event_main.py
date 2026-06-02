from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

import uvicorn
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

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


HTML = """
<!doctype html><html lang='zh-Hant'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>BTC 5m Event Prediction</title><style>
body{margin:0;background:#07111f;color:#eef5ff;font-family:system-ui}.wrap{max-width:900px;margin:auto;padding:16px}.card{background:#10213a;border:1px solid #2a405e;border-radius:18px;padding:14px;margin:12px 0}.row{display:flex;justify-content:space-between;gap:12px;padding:8px 0;border-bottom:1px solid rgba(159,178,204,.18)}.value{font-size:30px;font-weight:900}.muted{color:#9fb2cc}.mono{font-family:ui-monospace,monospace;font-size:12px;overflow-wrap:anywhere}a{color:#b9e6ff}
</style></head><body><main class='wrap'><h1>BTC 5m Event Prediction</h1><p class='muted'>只判斷 BTC 5 分鐘漲跌 · YES / NO / WAIT · Prediction only</p><section class='card'><div class='muted'>AI 判斷</div><div id='direction' class='value'>WAIT</div><div id='prob' class='muted'></div></section><section class='card'><h2>市場</h2><div class='row'><span>狀態</span><b id='status'>—</b></div><div class='row'><span>Slug</span><b id='slug' class='mono'>—</b></div><div class='row'><span>問題</span><b id='question' class='mono'>—</b></div></section><section class='card'><h2>來源</h2><pre id='sources' class='mono'></pre></section><section class='card'><div class='mono'><a href='/status'>/status</a> · <a href='/mode'>/mode</a> · <a href='/healthz'>/healthz</a> · <a href='/docs'>/docs</a></div></section></main><script>
const pct=x=>Number.isFinite(Number(x))?`${(Number(x)*100).toFixed(2)}%`:'—';async function refresh(){const r=await fetch('/status',{cache:'no-store'}).then(x=>x.json());document.getElementById('direction').textContent=r.ai.direction;document.getElementById('prob').textContent=`上漲機率 ${pct(r.ai.probability_up)} · 信心 ${pct(r.ai.confidence)} · Edge ${pct(r.ai.selected_edge)}`;document.getElementById('status').textContent=r.market.discovery_status;document.getElementById('slug').textContent=(r.market.current||{}).slug||'—';document.getElementById('question').textContent=(r.market.current||{}).question||'—';document.getElementById('sources').textContent=JSON.stringify(r.sources,null,2)}refresh();setInterval(refresh,2000)</script></body></html>
"""


def _secret_ready(value: str) -> bool:
    return bool(value and value != "change-me" and len(value) >= 16)


def build_mode_status() -> dict[str, Any]:
    return {
        "mode": "btc_5m_event_prediction_only",
        "execution": "prediction_only",
        "market": {"asset": "BTC", "interval_minutes": 5},
        "outputs": ["YES", "NO", "WAIT"],
        "safety": {
            "orders_enabled": False,
            "paper_positions_enabled": False,
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
        "sources": snapshot.get("source_status", {}),
        "fusion": fusion,
        "connections": snapshot.get("connections", {}),
        "last_error": snapshot.get("last_error"),
    }


async def run() -> None:
    settings = Settings()
    apply_balanced_btc5m_paper_profile(settings)
    state = BotState()

    async def evaluate() -> None:
        await state.record_event("prediction_evaluation")

    feeds = MeasuredFeedHub(settings, state, evaluate)
    fusion = MultiSourceFusion(settings, state, feeds)
    app = FastAPI(title="BTC 5m Event Prediction")

    @app.get("/", response_class=HTMLResponse)
    async def dashboard() -> HTMLResponse:
        return HTMLResponse(HTML)

    @app.get("/mode")
    async def mode() -> dict[str, Any]:
        return build_mode_status()

    @app.get("/status")
    async def status() -> dict[str, Any]:
        return await build_status(settings, state)

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
