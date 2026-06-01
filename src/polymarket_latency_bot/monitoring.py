from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse


MONITOR_HTML = r"""
<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Bot Monitor</title><style>
:root{--bg:#07111f;--panel:#10213a;--line:#2a405e;--text:#eef5ff;--muted:#9fb2cc;--green:#22c55e;--red:#ef4444;--yellow:#f59e0b}*{box-sizing:border-box}body{margin:0;background:#07111f;color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.wrap{max-width:1180px;margin:auto;padding:14px}.top{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.pill{padding:9px 12px;border-radius:99px;border:1px solid var(--line);font-weight:800}.ok{color:#b7f7ca;border-color:#2a9d55;background:#173b2a}.warn{color:#fde68a;border-color:#b7791f;background:#3d2d12}.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:12px;margin-top:14px}.card{grid-column:span 12;background:#10213a;border:1px solid var(--line);border-radius:18px;padding:14px}.half{grid-column:span 6}.third{grid-column:span 4}.title{font-size:16px;margin:0 0 10px}.row{display:flex;justify-content:space-between;gap:12px;padding:9px 0;border-bottom:1px solid rgba(159,178,204,.18)}.row:last-child{border:0}.muted{color:var(--muted)}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;overflow-wrap:anywhere}.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:7px;background:var(--red)}.dot.on{background:var(--green)}table{width:100%;border-collapse:collapse;font-size:12px}th,td{padding:8px 5px;text-align:left;border-bottom:1px solid rgba(159,178,204,.18)}th{color:var(--muted)}a{color:#b9e6ff}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}.src{padding:10px 0;border-bottom:1px solid rgba(159,178,204,.18)}.src:last-child{border-bottom:0}.tiny{font-size:11px;color:var(--muted);margin-top:4px;overflow-wrap:anywhere}@media(max-width:760px){.half,.third{grid-column:span 12}}
</style></head><body><main class="wrap"><div class="top"><div><h1>Hardened Paper Monitor</h1><div class="muted">多來源融合 · Order Book 深度 · VWAP · 滑點 · 拒絕原因</div></div><div id="overall" class="pill warn">載入中</div></div><section class="grid">
<article class="card third"><h2 class="title">資料來源</h2><div id="sources"></div></article>
<article class="card third"><h2 class="title">融合訊號</h2><div id="fusion"></div></article>
<article class="card third"><h2 class="title">系統</h2><div id="system"></div></article>
<article class="card half"><h2 class="title">策略拒絕原因</h2><div id="strategyRejects"></div></article>
<article class="card half"><h2 class="title">Paper 拒絕原因</h2><div id="paperRejects"></div></article>
<article class="card"><h2 class="title">Order Book 深度與 VWAP</h2><table><thead><tr><th>Token</th><th>Bid</th><th>Ask</th><th>Spread</th><th>Bid Depth</th><th>Ask Depth</th><th>VWAP $5</th><th>Slip</th></tr></thead><tbody id="books"></tbody></table></article>
<article class="card"><h2 class="title">最後策略判斷</h2><pre id="lastStrategy">—</pre></article>
<article class="card"><h2 class="title">API</h2><div class="mono"><a href="/" target="_blank">首頁</a> · <a href="/state" target="_blank">/state</a> · <a href="/readiness" target="_blank">/readiness</a> · <a href="/metrics" target="_blank">/metrics</a> · <a href="/debug/strategy" target="_blank">/debug/strategy</a> · <a href="/debug/rejections" target="_blank">/debug/rejections</a> · <a href="/debug/sources" target="_blank">/debug/sources</a> · <a href="/history" target="_blank">/history</a></div></article>
</section></main><script>
const $=x=>document.getElementById(x);const num=(x,d=4)=>Number.isFinite(Number(x))?Number(x).toFixed(d):'—';const pct=x=>Number.isFinite(Number(x))?`${(Number(x)*100).toFixed(2)}%`:'—';const row=(a,b)=>`<div class="row"><span>${a}</span><strong>${b}</strong></div>`;const age=ms=>Number.isFinite(Number(ms))?`${Math.round(Number(ms))} ms`:'—';
function rejects(obj){const entries=Object.entries(obj||{}).sort((a,b)=>b[1]-a[1]);return entries.length?entries.map(([k,v])=>row(k,v)).join(''):'<div class="muted">尚無拒絕紀錄</div>'}
function src(k,x){return `<div class="src"><div><i class="dot ${x.connected?'on':''}"></i><strong>${k}</strong> <span class="muted">${x.last_price?num(x.last_price,2):'—'} · ${age(x.age_ms)}</span></div><div class="tiny">端點：${x.active_endpoint||'—'}</div><div class="tiny">重連：${x.reconnect_count||0}</div><div class="tiny">錯誤：${x.last_error||'—'}</div></div>`}
async function refresh(){try{const [sr,cr]=await Promise.all([fetch('/state',{cache:'no-store'}),fetch('/config',{cache:'no-store'})]);if(!sr.ok||!cr.ok)throw Error('state offline');const s=await sr.json(),cfg=await cr.json(),st=s.source_status||{},f=s.fusion_snapshot||{},p=s.paper_portfolio||{};$('sources').innerHTML=['chainlink','binance','coinbase'].map(k=>src(k,st[k]||{})).join('');$('fusion').innerHTML=row('狀態',f.status||'—')+row('來源數',`${f.source_count||0}/${f.required_sources||2}`)+row('Agreement',pct(f.agreement))+row('上漲機率',pct(f.probability_up))+row('信心',pct(f.confidence))+row('Momentum',pct(f.fused_momentum));const c=s.connections||{};$('system').innerHTML=row('Market WS',c.market_ws?'ON':'OFF')+row('RTDS',c.rtds_ws?'ON':'OFF')+row('市場發現',s.market_discovery_status||'—')+row('訂單',s.orders_submitted||0)+row('拒絕',s.orders_rejected||0)+row('Queue',s.queue_depth||0)+row('策略節流',`${cfg.strategy_evaluation_interval_ms||'—'} ms`)+row('融合優先',cfg.prefer_fusion_prediction?'ON':'OFF');$('strategyRejects').innerHTML=rejects(s.strategy_rejections);$('paperRejects').innerHTML=rejects(p.rejection_counts);const books=Object.values(s.books||{});$('books').innerHTML=books.map(b=>{const bids=b.bid_levels||[],asks=b.ask_levels||[];const bidDepth=bids.slice(0,5).reduce((t,x)=>t+Number(x.price)*Number(x.size),0);const askDepth=asks.slice(0,5).reduce((t,x)=>t+Number(x.price)*Number(x.size),0);let remain=5,spent=0,shares=0;for(const x of asks.slice(0,5)){const usd=Math.min(remain,Number(x.price)*Number(x.size));spent+=usd;shares+=usd/Number(x.price);remain-=usd;if(remain<=1e-9)break}const vwap=remain<=1e-9&&shares>0?spent/shares:null;const slip=vwap!=null&&b.best_ask!=null?vwap-Number(b.best_ask):null;return `<tr><td class="mono">${String(b.token_id).slice(0,8)}…</td><td>${num(b.best_bid)}</td><td>${num(b.best_ask)}</td><td>${b.best_bid!=null&&b.best_ask!=null?num(Number(b.best_ask)-Number(b.best_bid)):'—'}</td><td>${num(bidDepth,2)}</td><td>${num(askDepth,2)}</td><td>${num(vwap)}</td><td>${num(slip)}</td></tr>`}).join('');$('lastStrategy').textContent=JSON.stringify(s.last_strategy_snapshot||{},null,2);const ready=c.market_ws&&c.rtds_ws&&s.market_discovery_status==='ready';$('overall').textContent=ready?'ACTIVE':'WAITING';$('overall').className=`pill ${ready?'ok':'warn'}`;}catch(e){$('overall').textContent='OFFLINE';$('overall').className='pill warn'}}refresh();setInterval(refresh,2000);
</script></body></html>
"""


def effective_config(settings: Any) -> dict[str, Any]:
    names = [
        "auto_discover_market", "account_equity_usd", "max_order_equity_fraction",
        "max_daily_loss_fraction", "max_open_notional_usd", "min_edge", "min_net_edge",
        "min_confidence", "min_contract_price", "max_contract_price", "max_spread",
        "depth_levels", "min_depth_multiple", "max_slippage", "slippage_buffer",
        "signal_cooldown_ms", "strategy_evaluation_interval_ms", "prefer_fusion_prediction",
        "paper_hold_sec", "paper_max_open_positions", "paper_take_profit_pct",
        "paper_stop_loss_pct", "paper_trailing_stop_pct", "paper_open_buffer_sec",
        "paper_close_buffer_sec", "paper_max_trades_per_market",
        "paper_max_consecutive_losses_per_market", "paper_db_path", "enable_binance_ws",
        "binance_ws_url", "binance_ws_fallback_urls", "enable_coinbase_ws",
        "coinbase_ws_url", "source_reconnect_delay_sec", "enable_multi_source_fusion",
        "fusion_min_sources", "fusion_agreement_threshold", "external_price_max_age_ms",
        "external_price_window_sec",
    ]
    output = {"mode": "paper"}
    for name in names:
        output[name] = getattr(settings, name)
    return output


def register_monitoring_routes(app: FastAPI, settings: Any, state: Any, risk: Any, portfolio: Any) -> None:
    @app.get("/monitor", response_class=HTMLResponse)
    async def monitor() -> HTMLResponse:
        return HTMLResponse(MONITOR_HTML)

    @app.get("/liveness")
    async def liveness() -> dict[str, Any]:
        return {"alive": True, "mode": "paper"}

    @app.get("/readiness")
    async def readiness() -> dict[str, Any]:
        snapshot = await state.snapshot(); c = snapshot["connections"]
        return {"ready": bool(snapshot["market_discovery_status"] == "ready" and c["market_ws"] and c["rtds_ws"]), "market_discovery_status": snapshot["market_discovery_status"], "connections": c, "source_status": snapshot["source_status"], "fusion": snapshot["fusion_snapshot"], "last_error": snapshot["last_error"]}

    @app.get("/metrics")
    async def metrics() -> dict[str, Any]:
        snapshot = await state.snapshot()
        return {"mode": "paper", "uptime_ms": snapshot["uptime_ms"], "orders_submitted": snapshot["orders_submitted"], "orders_rejected": snapshot["orders_rejected"], "queue_depth": snapshot["queue_depth"], "connections": snapshot["connections"], "source_status": snapshot["source_status"], "fusion": snapshot["fusion_snapshot"], "paper_summary": snapshot["paper_portfolio"].get("summary", {}), "risk": asdict(risk.snapshot), "last_order_result": snapshot["last_order_result"]}

    @app.get("/debug/strategy")
    async def debug_strategy() -> dict[str, Any]:
        snapshot = await state.snapshot()
        return {"market": snapshot["current_market"], "books": snapshot["books"], "predictions": snapshot["predictions"], "fusion": snapshot["fusion_snapshot"], "last_strategy_snapshot": snapshot["last_strategy_snapshot"], "config": effective_config(settings)}

    @app.get("/debug/rejections")
    async def debug_rejections() -> dict[str, Any]:
        snapshot = await state.snapshot(); paper = snapshot["paper_portfolio"]
        return {"strategy_rejections": snapshot["strategy_rejections"], "paper_rejections": paper.get("rejection_counts", {}), "rules": paper.get("rules", {}), "orders_rejected": snapshot["orders_rejected"]}

    @app.get("/debug/sources")
    async def debug_sources() -> dict[str, Any]:
        snapshot = await state.snapshot()
        return {"source_status": snapshot["source_status"], "fusion": snapshot["fusion_snapshot"], "connections": snapshot["connections"]}

    @app.get("/history")
    async def history() -> dict[str, Any]:
        return {"summary": portfolio.store.summary(), "recent_trades": portfolio.store.recent_trades(settings.recent_trade_limit), "db_path": portfolio.store.db_path}

    app.add_api_route("/config", lambda: effective_config(settings), methods=["GET"])
