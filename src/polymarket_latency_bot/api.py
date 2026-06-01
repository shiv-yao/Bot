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
  <meta name="theme-color" content="#09111f" />
  <title>Polymarket Latency Bot</title>
  <style>
    :root {
      --bg: #07111f;
      --panel: rgba(15, 27, 46, 0.88);
      --panel-2: rgba(20, 36, 59, 0.92);
      --line: rgba(148, 163, 184, 0.20);
      --text: #e5edf8;
      --muted: #91a4bd;
      --green: #22c55e;
      --yellow: #f59e0b;
      --red: #ef4444;
      --blue: #38bdf8;
      --purple: #a78bfa;
      --radius: 18px;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--text);
      background:
        radial-gradient(circle at 10% 0%, rgba(56, 189, 248, .14), transparent 34%),
        radial-gradient(circle at 90% 15%, rgba(167, 139, 250, .12), transparent 30%),
        var(--bg);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      min-height: 100vh;
    }
    .wrap { max-width: 1180px; margin: 0 auto; padding: 18px; }
    .topbar { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; margin: 5px 0 18px; }
    h1 { font-size: clamp(22px, 4vw, 34px); margin:0; letter-spacing:.2px; }
    .subtitle { color: var(--muted); margin-top:7px; font-size:14px; }
    .pill { padding:8px 12px; border-radius:999px; font-size:12px; font-weight:700; letter-spacing:.4px; border:1px solid var(--line); white-space:nowrap; }
    .ok { color:#bbf7d0; background:rgba(34,197,94,.14); border-color:rgba(34,197,94,.4); }
    .warn { color:#fde68a; background:rgba(245,158,11,.14); border-color:rgba(245,158,11,.4); }
    .bad { color:#fecaca; background:rgba(239,68,68,.14); border-color:rgba(239,68,68,.4); }
    .grid { display:grid; grid-template-columns:repeat(12, minmax(0,1fr)); gap:14px; }
    .card { background:linear-gradient(180deg,var(--panel-2),var(--panel)); border:1px solid var(--line); border-radius:var(--radius); padding:15px; box-shadow:0 12px 28px rgba(0,0,0,.18); }
    .span-3 { grid-column:span 3; } .span-4 { grid-column:span 4; } .span-6 { grid-column:span 6; } .span-8 { grid-column:span 8; } .span-12 { grid-column:span 12; }
    .label { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.8px; }
    .value { margin-top:7px; font-size:26px; font-weight:800; overflow-wrap:anywhere; }
    .small { font-size:14px; color:var(--muted); margin-top:6px; line-height:1.5; }
    .section-title { margin:0 0 12px; font-size:16px; }
    .row { display:flex; align-items:center; justify-content:space-between; gap:10px; padding:9px 0; border-bottom:1px solid rgba(148,163,184,.12); }
    .row:last-child { border-bottom:0; }
    .dot { display:inline-block; width:9px; height:9px; border-radius:99px; margin-right:7px; background:var(--red); box-shadow:0 0 16px rgba(239,68,68,.6); }
    .dot.on { background:var(--green); box-shadow:0 0 16px rgba(34,197,94,.7); }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size:12px; overflow-wrap:anywhere; }
    table { width:100%; border-collapse:collapse; font-size:13px; }
    th, td { text-align:left; padding:9px 6px; border-bottom:1px solid rgba(148,163,184,.12); vertical-align:top; }
    th { color:var(--muted); font-weight:600; }
    .bar { height:9px; background:rgba(148,163,184,.18); border-radius:99px; overflow:hidden; margin-top:9px; }
    .bar > i { display:block; height:100%; background:linear-gradient(90deg,var(--blue),var(--purple)); border-radius:99px; }
    .links { display:flex; gap:8px; flex-wrap:wrap; margin-top:14px; }
    .btn { text-decoration:none; color:var(--text); border:1px solid var(--line); padding:8px 11px; border-radius:10px; font-size:12px; background:rgba(255,255,255,.04); }
    .btn:hover { background:rgba(255,255,255,.08); }
    .footer { color:var(--muted); font-size:12px; margin:18px 2px 5px; text-align:center; }
    @media (max-width: 880px) { .span-3,.span-4 { grid-column:span 6; } .span-8 { grid-column:span 12; } }
    @media (max-width: 560px) { .wrap{padding:12px;} .card{padding:13px;} .span-3,.span-4,.span-6 { grid-column:span 12; } .value{font-size:22px;} .topbar{align-items:flex-start;} }
  </style>
</head>
<body>
  <main class="wrap">
    <div class="topbar">
      <div>
        <h1>Polymarket Latency Bot</h1>
        <div class="subtitle">BTC 15 分鐘市場 · 自動換約 · Paper 模式監控</div>
      </div>
      <div id="overall" class="pill warn">載入中</div>
    </div>

    <section class="grid">
      <article class="card span-3"><div class="label">執行模式</div><div id="mode" class="value">—</div><div class="small">目前固定為 Paper，不會送出真實訂單。</div></article>
      <article class="card span-3"><div class="label">BTC 即時價格</div><div id="btc" class="value">—</div><div id="btcTime" class="small">等待 RTDS 資料</div></article>
      <article class="card span-3"><div class="label">Paper 訂單</div><div id="orders" class="value">0</div><div id="rejected" class="small">拒絕：0</div></article>
      <article class="card span-3"><div class="label">最後執行延遲</div><div id="latency" class="value">—</div><div id="queue" class="small">Queue：0</div></article>

      <article class="card span-8">
        <h2 class="section-title">目前 BTC 15 分鐘市場</h2>
        <div class="row"><span>市場發現狀態</span><strong id="discovery">—</strong></div>
        <div class="row"><span>Slug</span><strong id="slug" class="mono">—</strong></div>
        <div class="row"><span>問題</span><strong id="question">—</strong></div>
        <div class="row"><span>Condition ID</span><strong id="condition" class="mono">—</strong></div>
        <div class="row"><span>YES / UP Token</span><strong id="yesToken" class="mono">—</strong></div>
        <div class="row"><span>NO / DOWN Token</span><strong id="noToken" class="mono">—</strong></div>
      </article>

      <article class="card span-4">
        <h2 class="section-title">連線狀態</h2>
        <div class="row"><span><i id="dotRtds" class="dot"></i>BTC RTDS</span><strong id="rtds">OFF</strong></div>
        <div class="row"><span><i id="dotMarket" class="dot"></i>Market WS</span><strong id="marketWs">OFF</strong></div>
        <div class="row"><span><i id="dotUser" class="dot"></i>User WS</span><strong id="userWs">OFF</strong></div>
        <div class="small">User WS 在 Paper 模式顯示 OFF 屬正常狀態。</div>
      </article>

      <article class="card span-6">
        <h2 class="section-title">最新預測</h2>
        <div id="predictionEmpty" class="small">尚未收到預測訊號。</div>
        <table id="predictionTable" style="display:none">
          <thead><tr><th>來源</th><th>上漲機率</th><th>信心</th><th>更新</th></tr></thead>
          <tbody id="predictionBody"></tbody>
        </table>
      </article>

      <article class="card span-6">
        <h2 class="section-title">風控</h2>
        <div class="row"><span>今日已實現 PnL</span><strong id="pnl">—</strong></div>
        <div class="row"><span>目前曝險</span><strong id="exposure">—</strong></div>
        <div class="row"><span>日內止損狀態</span><strong id="halted">—</strong></div>
        <div class="bar"><i id="lossBar" style="width:0%"></i></div>
        <div id="lossText" class="small">等待風控資料</div>
      </article>

      <article class="card span-12">
        <h2 class="section-title">最後一筆策略意圖 / 模擬執行</h2>
        <pre id="lastIntent" class="mono">尚未觸發交易訊號。</pre>
        <div class="links">
          <a class="btn" href="/health" target="_blank">/health</a>
          <a class="btn" href="/state" target="_blank">/state</a>
          <a class="btn" href="/risk" target="_blank">/risk</a>
          <a class="btn" href="/docs" target="_blank">API Docs</a>
        </div>
      </article>
    </section>
    <div id="updated" class="footer">等待更新</div>
  </main>
<script>
  const $ = (id) => document.getElementById(id);
  const fmtUsd = (n) => Number.isFinite(Number(n)) ? `$${Number(n).toFixed(2)}` : '—';
  const fmtPct = (n) => Number.isFinite(Number(n)) ? `${(Number(n) * 100).toFixed(2)}%` : '—';
  const ago = (ms) => {
    if (!ms) return '—';
    const seconds = Math.max(0, Math.floor((Date.now() - Number(ms))/1000));
    if (seconds < 60) return `${seconds}s 前`;
    return `${Math.floor(seconds/60)}m 前`;
  };
  const short = (value) => {
    const text = String(value || '—');
    return text.length > 28 ? `${text.slice(0,14)}…${text.slice(-10)}` : text;
  };
  const setConn = (key, value) => {
    $(key).textContent = value ? 'ON' : 'OFF';
    $(`dot${key[0].toUpperCase()}${key.slice(1)}`).classList.toggle('on', !!value);
  };
  async function refresh() {
    try {
      const [stateRes, riskRes, healthRes] = await Promise.all([
        fetch('/state', {cache:'no-store'}),
        fetch('/risk', {cache:'no-store'}),
        fetch('/health', {cache:'no-store'})
      ]);
      if (!stateRes.ok || !riskRes.ok || !healthRes.ok) throw new Error('API 回應異常');
      const state = await stateRes.json();
      const risk = await riskRes.json();
      const health = await healthRes.json();
      $('mode').textContent = String(health.mode || 'paper').toUpperCase();
      $('orders').textContent = state.orders_submitted ?? 0;
      $('rejected').textContent = `拒絕：${state.orders_rejected ?? 0}`;
      $('queue').textContent = `Queue：${state.queue_depth ?? 0}`;
      $('latency').textContent = state.last_order_result?.latency_ms != null ? `${state.last_order_result.latency_ms} ms` : '—';

      const prices = state.btc_prices_tail || [];
      const latest = prices.length ? prices[prices.length - 1] : null;
      $('btc').textContent = latest ? `$${Number(latest[1]).toLocaleString(undefined,{maximumFractionDigits:2})}` : '—';
      $('btcTime').textContent = latest ? `更新：${ago(latest[0])}` : '等待 RTDS 資料';

      const market = state.current_market || {};
      $('discovery').textContent = state.market_discovery_status || '—';
      $('slug').textContent = market.slug || '—';
      $('question').textContent = market.question || '—';
      $('condition').textContent = short(market.condition_id);
      $('yesToken').textContent = short(market.yes_token_id);
      $('noToken').textContent = short(market.no_token_id);

      const connections = state.connections || {};
      setConn('rtds', connections.rtds_ws);
      setConn('marketWs', connections.market_ws);
      setConn('userWs', connections.user_ws);

      const predictions = Object.values(state.predictions || {}).sort((a,b)=>(b.timestamp_ms||0)-(a.timestamp_ms||0));
      $('predictionEmpty').style.display = predictions.length ? 'none' : 'block';
      $('predictionTable').style.display = predictions.length ? 'table' : 'none';
      $('predictionBody').innerHTML = predictions.map(p => `<tr><td>${p.source}</td><td>${fmtPct(p.probability_up)}</td><td>${fmtPct(p.confidence)}</td><td>${ago(p.timestamp_ms)}</td></tr>`).join('');

      $('pnl').textContent = fmtUsd(risk.realized_pnl);
      $('exposure').textContent = fmtUsd(risk.open_notional);
      $('halted').textContent = risk.halted ? `已停止：${risk.halt_reason || '風控觸發'}` : '正常';
      const maxLoss = Number(risk.day_start_equity || 0) * 0.02;
      const used = maxLoss > 0 ? Math.min(100, Math.max(0, (-Number(risk.realized_pnl || 0) / maxLoss) * 100)) : 0;
      $('lossBar').style.width = `${used}%`;
      $('lossText').textContent = `日損額度使用：${used.toFixed(1)}%`;

      $('lastIntent').textContent = JSON.stringify({ intent: state.last_intent, result: state.last_order_result, error: state.last_error }, null, 2);
      const ready = connections.rtds_ws && (state.market_discovery_status === 'ready' || connections.market_ws);
      $('overall').textContent = ready ? 'SYSTEM ACTIVE' : 'WAITING FOR DATA';
      $('overall').className = `pill ${ready ? 'ok' : 'warn'}`;
      $('updated').textContent = `最後更新：${new Date().toLocaleTimeString()} · 每 2 秒自動刷新`;
    } catch (err) {
      $('overall').textContent = 'API OFFLINE';
      $('overall').className = 'pill bad';
      $('updated').textContent = `讀取失敗：${err.message}`;
    }
  }
  refresh();
  setInterval(refresh, 2000);
</script>
</body>
</html>
"""


def create_app(settings: Settings, state: BotState, feeds: FeedHub, risk: RiskManager) -> FastAPI:
    app = FastAPI(title="Polymarket Latency Bot", version="0.2.0")

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
