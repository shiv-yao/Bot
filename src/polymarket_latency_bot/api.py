from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
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


DASHBOARD_HTML = r"""
<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="theme-color" content="#07111f" />
  <title>Polymarket Latency Bot</title>
  <style>
    :root{--bg:#07111f;--panel:#10213a;--line:#2a405e;--text:#eef5ff;--muted:#9fb2cc;--green:#22c55e;--yellow:#f59e0b;--red:#ef4444;--blue:#38bdf8}
    *{box-sizing:border-box}body{margin:0;background:linear-gradient(180deg,#0a1a2d,#07111f);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;min-height:100vh}.wrap{max-width:1100px;margin:auto;padding:18px}.top{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;margin-bottom:16px}h1{margin:0;font-size:clamp(26px,5vw,40px)}.sub{color:var(--muted);margin-top:8px;line-height:1.5}.pill{padding:10px 14px;border-radius:999px;font-weight:800;font-size:13px;border:1px solid}.ok{color:#b7f7ca;border-color:#2a9d55;background:#173b2a}.warn{color:#fde68a;border-color:#b7791f;background:#3d2d12}.bad{color:#fecaca;border-color:#b33a3a;background:#421d24}.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:14px}.card{grid-column:span 12;background:linear-gradient(180deg,#132844,#0f2037);border:1px solid var(--line);border-radius:20px;padding:16px}.mini{grid-column:span 3}.half{grid-column:span 6}.title{font-size:15px;font-weight:800;margin:0 0 10px}.label{color:var(--muted);font-size:13px;letter-spacing:.5px}.value{font-size:28px;font-weight:900;margin-top:8px}.small{color:var(--muted);font-size:13px;margin-top:7px;line-height:1.5}.row{display:flex;justify-content:space-between;gap:12px;padding:10px 0;border-bottom:1px solid rgba(159,178,204,.18)}.row:last-child{border-bottom:0}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;overflow-wrap:anywhere;text-align:right}.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:8px;background:var(--red)}.dot.on{background:var(--green);box-shadow:0 0 12px rgba(34,197,94,.8)}table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:9px 5px;border-bottom:1px solid rgba(159,178,204,.18)}th{color:var(--muted)}pre{white-space:pre-wrap;overflow-wrap:anywhere;color:#d8e7fa;font-size:12px}.links{display:flex;gap:8px;flex-wrap:wrap}.btn{color:var(--text);text-decoration:none;border:1px solid var(--line);padding:8px 10px;border-radius:10px;font-size:12px}.foot{text-align:center;color:var(--muted);font-size:12px;margin-top:16px}@media(max-width:760px){.mini,.half{grid-column:span 12}.wrap{padding:12px}.top{align-items:flex-start}.value{font-size:25px}}
  </style>
</head>
<body>
<main class="wrap">
  <div class="top"><div><h1>Polymarket Latency Bot</h1><div class="sub">BTC 15 分鐘市場 · 自動換約 · Paper 模式監控</div></div><div id="overall" class="pill warn">載入中</div></div>
  <section class="grid">
    <article class="card mini"><div class="label">執行模式</div><div id="mode" class="value">—</div><div class="small">Paper 模式不送出真實訂單</div></article>
    <article class="card mini"><div class="label">BTC 即時價格</div><div id="btc" class="value">—</div><div id="btcTime" class="small">等待資料</div></article>
    <article class="card mini"><div class="label">Paper 訂單</div><div id="orders" class="value">0</div><div id="rejected" class="small">拒絕：0</div></article>
    <article class="card mini"><div class="label">最後延遲</div><div id="latency" class="value">—</div><div id="queue" class="small">Queue：0</div></article>

    <article class="card half"><h2 class="title">目前 BTC 15 分鐘市場</h2>
      <div class="row"><span>市場發現</span><strong id="discovery">—</strong></div>
      <div class="row"><span>Slug</span><strong id="slug" class="mono">—</strong></div>
      <div class="row"><span>問題</span><strong id="question" class="mono">—</strong></div>
      <div class="row"><span>Condition ID</span><strong id="condition" class="mono">—</strong></div>
      <div class="row"><span>YES / UP</span><strong id="yesToken" class="mono">—</strong></div>
      <div class="row"><span>NO / DOWN</span><strong id="noToken" class="mono">—</strong></div>
    </article>

    <article class="card half"><h2 class="title">連線狀態</h2>
      <div class="row"><span><i id="dotRtds" class="dot"></i>BTC RTDS</span><strong id="rtds">OFF</strong></div>
      <div class="row"><span><i id="dotMarket" class="dot"></i>Market WS</span><strong id="marketWs">OFF</strong></div>
      <div class="row"><span><i id="dotUser" class="dot"></i>User WS</span><strong id="userWs">OFF</strong></div>
      <div class="small">User WS 在 Paper 模式顯示 OFF 屬正常狀態。</div>
    </article>

    <article class="card half"><h2 class="title">最新預測</h2><div id="predictionEmpty" class="small">尚未收到預測訊號。</div><table id="predictionTable" style="display:none"><thead><tr><th>來源</th><th>上漲機率</th><th>信心</th><th>更新</th></tr></thead><tbody id="predictionBody"></tbody></table></article>
    <article class="card half"><h2 class="title">風控</h2><div class="row"><span>已實現 PnL</span><strong id="pnl">—</strong></div><div class="row"><span>目前曝險</span><strong id="exposure">—</strong></div><div class="row"><span>止損狀態</span><strong id="halted">—</strong></div></article>
    <article class="card"><h2 class="title">最後一筆策略意圖 / 執行</h2><pre id="lastIntent">尚未觸發交易訊號。</pre><div class="links"><a class="btn" href="/health" target="_blank">/health</a><a class="btn" href="/state" target="_blank">/state</a><a class="btn" href="/risk" target="_blank">/risk</a><a class="btn" href="/docs" target="_blank">API Docs</a></div></article>
  </section>
  <div id="updated" class="foot">等待更新</div>
</main>
<script>
const $=id=>document.getElementById(id);
const fmtUsd=n=>Number.isFinite(Number(n))?`$${Number(n).toFixed(2)}`:'—';
const fmtPct=n=>Number.isFinite(Number(n))?`${(Number(n)*100).toFixed(2)}%`:'—';
const ago=ms=>{if(!ms)return'—';const s=Math.max(0,Math.floor((Date.now()-Number(ms))/1000));return s<60?`${s}s 前`:`${Math.floor(s/60)}m 前`};
const short=v=>{const t=String(v||'—');return t.length>28?`${t.slice(0,14)}…${t.slice(-10)}`:t};
const dotIds={rtds:'dotRtds',marketWs:'dotMarket',userWs:'dotUser'};
function setConn(key,value){$(key).textContent=value?'ON':'OFF';const dot=$(dotIds[key]);if(dot)dot.classList.toggle('on',!!value)}
async function refresh(){
 try{
  const [sr,rr,hr]=await Promise.all([fetch('/state',{cache:'no-store'}),fetch('/risk',{cache:'no-store'}),fetch('/health',{cache:'no-store'})]);
  if(!sr.ok||!rr.ok||!hr.ok)throw new Error('API 回應異常');
  const state=await sr.json(),risk=await rr.json(),health=await hr.json();
  $('mode').textContent=String(health.mode||'paper').toUpperCase();$('orders').textContent=state.orders_submitted??0;$('rejected').textContent=`拒絕：${state.orders_rejected??0}`;$('queue').textContent=`Queue：${state.queue_depth??0}`;$('latency').textContent=state.last_order_result?.latency_ms!=null?`${state.last_order_result.latency_ms} ms`:'—';
  const prices=state.btc_prices_tail||[],latest=prices.length?prices[prices.length-1]:null;$('btc').textContent=latest?`$${Number(latest[1]).toLocaleString(undefined,{maximumFractionDigits:2})}`:'—';$('btcTime').textContent=latest?`更新：${ago(latest[0])}`:'等待 RTDS 資料';
  const m=state.current_market||{};$('discovery').textContent=state.market_discovery_status||'—';$('slug').textContent=m.slug||'—';$('question').textContent=m.question||'—';$('condition').textContent=short(m.condition_id);$('yesToken').textContent=short(m.yes_token_id);$('noToken').textContent=short(m.no_token_id);
  const c=state.connections||{};setConn('rtds',c.rtds_ws);setConn('marketWs',c.market_ws);setConn('userWs',c.user_ws);
  const ps=Object.values(state.predictions||{}).sort((a,b)=>(b.timestamp_ms||0)-(a.timestamp_ms||0));$('predictionEmpty').style.display=ps.length?'none':'block';$('predictionTable').style.display=ps.length?'table':'none';$('predictionBody').innerHTML=ps.map(p=>`<tr><td>${p.source}</td><td>${fmtPct(p.probability_up)}</td><td>${fmtPct(p.confidence)}</td><td>${ago(p.timestamp_ms)}</td></tr>`).join('');
  $('pnl').textContent=fmtUsd(risk.realized_pnl);$('exposure').textContent=fmtUsd(risk.open_notional);$('halted').textContent=risk.halted?`已停止：${risk.halt_reason||'風控觸發'}`:'正常';$('lastIntent').textContent=JSON.stringify({intent:state.last_intent,result:state.last_order_result,error:state.last_error},null,2);
  const active=!!(c.rtds_ws&&c.market_ws&&state.market_discovery_status==='ready');$('overall').textContent=active?'SYSTEM ACTIVE':'WAITING FOR DATA';$('overall').className=`pill ${active?'ok':'warn'}`;$('updated').textContent=`最後更新：${new Date().toLocaleTimeString()} · 每 2 秒刷新`;
 }catch(err){$('overall').textContent='API OFFLINE';$('overall').className='pill bad';$('updated').textContent=`讀取失敗：${err.message}`}
}
refresh();setInterval(refresh,2000);
</script>
</body>
</html>
"""


def create_app(settings: Settings, state: BotState, feeds: FeedHub, risk: RiskManager) -> FastAPI:
    app = FastAPI(title="Polymarket Latency Bot", version="0.2.1")

    @app.get("/", response_class=HTMLResponse)
    async def dashboard() -> HTMLResponse:
        return HTMLResponse(DASHBOARD_HTML)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "ok": True,
            "mode": "paper",
            "market_ws_required": bool(settings.yes_token_id and settings.no_token_id),
            "auto_discover_market": settings.auto_discover_market,
        }

    @app.get("/state")
    async def state_snapshot() -> dict[str, Any]:
        return await state.snapshot()

    @app.get("/risk")
    async def risk_snapshot() -> dict[str, Any]:
        async with risk.lock:
            return asdict(risk.snapshot)

    @app.post("/feeds/prediction")
    async def prediction_webhook(body: PredictionIn, x_webhook_secret: str = Header(default="")) -> dict[str, Any]:
        if x_webhook_secret != settings.webhook_secret:
            raise HTTPException(status_code=401, detail="invalid webhook secret")
        await feeds.upsert_prediction(Prediction(source=body.source, probability_up=body.probability_up, confidence=body.confidence, timestamp_ms=body.timestamp_ms or now_ms()))
        return {"accepted": True}

    @app.post("/risk/pnl-adjustment")
    async def pnl_adjustment(body: PnlAdjustmentIn, x_webhook_secret: str = Header(default="")) -> dict[str, Any]:
        if x_webhook_secret != settings.webhook_secret:
            raise HTTPException(status_code=401, detail="invalid webhook secret")
        snapshot = await risk.manual_pnl_adjustment(body.delta_usd)
        return asdict(snapshot)

    return app
