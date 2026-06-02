from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

import aiohttp
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .models import now_ms


EVENT_UI = r"""
<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Event Prediction Paper</title><style>
body{margin:0;background:#07111f;color:#eef5ff;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}.wrap{max-width:1100px;margin:auto;padding:14px}.card{background:#10213a;border:1px solid #2a405e;border-radius:18px;padding:14px;margin:12px 0}.row{display:grid;grid-template-columns:2fr .7fr .7fr .8fr;gap:8px;padding:10px 0;border-bottom:1px solid rgba(159,178,204,.18)}.muted{color:#9fb2cc}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;overflow-wrap:anywhere}a{color:#b9e6ff}@media(max-width:760px){.row{grid-template-columns:1fr}.hide-mobile{display:none}}
</style></head><body><main class="wrap"><h1>Event Prediction Paper</h1><p class="muted">事件市場掃描 · AI YES / NO / WAIT · Paper only</p><div class="card"><div class="mono"><a href="/dashboard5m">BTC 5m</a> · <a href="/event-prediction/status">status</a> · <a href="/event-prediction/markets">markets</a> · <a href="/event-prediction/signals">signals</a> · <a href="/docs">API Docs</a></div></div><div class="card"><h2>候選事件</h2><div id="list">載入中</div></div></main><script>
const pct=x=>Number.isFinite(Number(x))?`${(Number(x)*100).toFixed(2)}%`:'—';const usd=x=>Number.isFinite(Number(x))?`$${Number(x).toLocaleString(undefined,{maximumFractionDigits:0})}`:'—';async function refresh(){const r=await fetch('/event-prediction/markets?limit=40',{cache:'no-store'}).then(x=>x.json());document.getElementById('list').innerHTML=r.markets.map(x=>`<div class="row"><div><b>${x.question}</b><div class="mono muted">${x.slug}</div></div><span>YES ${pct(x.market_yes_price)}</span><span>流動性 ${usd(x.liquidity)}</span><span>24h量 ${usd(x.volume_24hr)}</span></div>`).join('')||'<div class="muted">尚未掃描到候選事件</div>'}refresh();setInterval(refresh,10000)</script></body></html>
"""


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _iso_to_ms(value: Any) -> int | None:
    if not value:
        return None
    try:
        text = str(value).replace('Z', '+00:00')
        return int(datetime.fromisoformat(text).timestamp() * 1000)
    except ValueError:
        return None


@dataclass(slots=True)
class EventCandidate:
    event_slug: str
    slug: str
    question: str
    condition_id: str
    yes_token_id: str
    no_token_id: str
    market_yes_price: float
    market_no_price: float
    liquidity: float
    volume_24hr: float
    end_date_ms: int | None
    scanned_ms: int


@dataclass(slots=True)
class EventForecast:
    market_slug: str
    source: str
    probability_yes: float
    confidence: float
    rationale: str
    timestamp_ms: int


class ForecastIn(BaseModel):
    market_slug: str = Field(min_length=1, max_length=240)
    probability_yes: float = Field(ge=0, le=1)
    confidence: float = Field(default=0.7, ge=0, le=1)
    source: str = Field(default='external_ai', min_length=1, max_length=64)
    rationale: str = Field(default='', max_length=2000)
    timestamp_ms: int | None = None


class EventPredictionEngine:
    def __init__(self, gamma_api_url: str, webhook_secret: str) -> None:
        self.gamma_api_url = gamma_api_url.rstrip('/')
        self.webhook_secret = webhook_secret
        self.lock = asyncio.Lock()
        self.markets: dict[str, EventCandidate] = {}
        self.forecasts: dict[str, EventForecast] = {}
        self.last_scan_ms: int | None = None
        self.last_error: str | None = None
        self.scan_interval_sec = 60.0
        self.min_liquidity = 1000.0
        self.min_volume_24hr = 250.0
        self.min_confidence = 0.65
        self.min_edge = 0.05

    def _secret_ready(self) -> bool:
        return bool(self.webhook_secret and self.webhook_secret != 'change-me' and len(self.webhook_secret) >= 16)

    async def scan_once(self) -> int:
        params = {
            'active': 'true',
            'closed': 'false',
            'order': 'volume_24hr',
            'ascending': 'false',
            'limit': '100',
        }
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f'{self.gamma_api_url}/events', params=params) as response:
                response.raise_for_status()
                payload = await response.json()
        found: dict[str, EventCandidate] = {}
        scanned_ms = now_ms()
        for event in payload if isinstance(payload, list) else []:
            event_slug = str(event.get('slug') or '')
            for market in event.get('markets') or []:
                if not market.get('active', True) or market.get('closed', False):
                    continue
                outcomes = [str(item).lower() for item in _list(market.get('outcomes'))]
                token_ids = [str(item) for item in _list(market.get('clobTokenIds') or market.get('clob_token_ids'))]
                prices = [_float(item) for item in _list(market.get('outcomePrices') or market.get('outcome_prices'))]
                if len(outcomes) != 2 or len(token_ids) != 2 or len(prices) != 2:
                    continue
                if set(outcomes) != {'yes', 'no'}:
                    continue
                yes_index = outcomes.index('yes')
                no_index = outcomes.index('no')
                liquidity = _float(market.get('liquidityNum') or market.get('liquidity'))
                volume_24hr = _float(market.get('volume24hr') or market.get('volume_24hr'))
                if liquidity < self.min_liquidity or volume_24hr < self.min_volume_24hr:
                    continue
                slug = str(market.get('slug') or '')
                if not slug:
                    continue
                found[slug] = EventCandidate(
                    event_slug=event_slug,
                    slug=slug,
                    question=str(market.get('question') or ''),
                    condition_id=str(market.get('conditionId') or market.get('condition_id') or ''),
                    yes_token_id=token_ids[yes_index],
                    no_token_id=token_ids[no_index],
                    market_yes_price=prices[yes_index],
                    market_no_price=prices[no_index],
                    liquidity=liquidity,
                    volume_24hr=volume_24hr,
                    end_date_ms=_iso_to_ms(market.get('endDate') or market.get('end_date')),
                    scanned_ms=scanned_ms,
                )
        async with self.lock:
            self.markets = found
            self.last_scan_ms = scanned_ms
            self.last_error = None
        return len(found)

    async def loop(self) -> None:
        while True:
            try:
                await self.scan_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                async with self.lock:
                    self.last_error = str(exc)
            await asyncio.sleep(self.scan_interval_sec)

    async def upsert_forecast(self, body: ForecastIn) -> EventForecast:
        forecast = EventForecast(
            market_slug=body.market_slug,
            source=body.source,
            probability_yes=body.probability_yes,
            confidence=body.confidence,
            rationale=body.rationale,
            timestamp_ms=body.timestamp_ms or now_ms(),
        )
        async with self.lock:
            self.forecasts[forecast.market_slug] = forecast
        return forecast

    async def status(self) -> dict[str, Any]:
        async with self.lock:
            return {
                'mode': 'paper',
                'engine': 'event_prediction',
                'markets': len(self.markets),
                'forecasts': len(self.forecasts),
                'last_scan_ms': self.last_scan_ms,
                'last_error': self.last_error,
                'scan_interval_sec': self.scan_interval_sec,
                'filters': {
                    'min_liquidity': self.min_liquidity,
                    'min_volume_24hr': self.min_volume_24hr,
                    'min_confidence': self.min_confidence,
                    'min_edge': self.min_edge,
                },
            }

    async def market_list(self, limit: int) -> list[dict[str, Any]]:
        async with self.lock:
            values = [asdict(item) for item in self.markets.values()]
        values.sort(key=lambda item: (item['volume_24hr'], item['liquidity']), reverse=True)
        return values[:limit]

    async def signals(self, limit: int) -> list[dict[str, Any]]:
        async with self.lock:
            markets = dict(self.markets)
            forecasts = dict(self.forecasts)
        output: list[dict[str, Any]] = []
        for slug, forecast in forecasts.items():
            market = markets.get(slug)
            if market is None:
                continue
            yes_edge = forecast.probability_yes - market.market_yes_price
            no_edge = (1 - forecast.probability_yes) - market.market_no_price
            direction = 'WAIT'
            edge = max(yes_edge, no_edge)
            if forecast.confidence >= self.min_confidence and edge >= self.min_edge:
                direction = 'BUY_YES' if yes_edge >= no_edge else 'BUY_NO'
            output.append({
                'market_slug': slug,
                'question': market.question,
                'direction': direction,
                'probability_yes': forecast.probability_yes,
                'confidence': forecast.confidence,
                'market_yes_price': market.market_yes_price,
                'market_no_price': market.market_no_price,
                'yes_edge': round(yes_edge, 6),
                'no_edge': round(no_edge, 6),
                'selected_edge': round(edge, 6),
                'source': forecast.source,
                'rationale': forecast.rationale,
                'timestamp_ms': forecast.timestamp_ms,
            })
        output.sort(key=lambda item: item['selected_edge'], reverse=True)
        return output[:limit]


def register_event_prediction_routes(app: FastAPI, engine: EventPredictionEngine) -> None:
    @app.get('/event-prediction/ui', response_class=HTMLResponse)
    async def event_prediction_ui() -> HTMLResponse:
        return HTMLResponse(EVENT_UI)

    @app.get('/event-prediction/status')
    async def event_prediction_status() -> dict[str, Any]:
        return await engine.status()

    @app.get('/event-prediction/markets')
    async def event_prediction_markets(limit: int = Query(default=50, ge=1, le=500)) -> dict[str, Any]:
        markets = await engine.market_list(limit)
        return {'mode': 'paper', 'count': len(markets), 'markets': markets}

    @app.get('/event-prediction/signals')
    async def event_prediction_signals(limit: int = Query(default=50, ge=1, le=500)) -> dict[str, Any]:
        signals = await engine.signals(limit)
        return {'mode': 'paper', 'count': len(signals), 'signals': signals}

    @app.post('/event-prediction/prediction')
    async def event_prediction_prediction(body: ForecastIn, x_webhook_secret: str = Header(default='')) -> dict[str, Any]:
        if not engine._secret_ready():
            raise HTTPException(status_code=503, detail='WEBHOOK_SECRET is not configured')
        if x_webhook_secret != engine.webhook_secret:
            raise HTTPException(status_code=401, detail='invalid webhook secret')
        forecast = await engine.upsert_forecast(body)
        return {'accepted': True, 'mode': 'paper', 'forecast': asdict(forecast)}
