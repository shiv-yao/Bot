from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse


HTML = r"""
<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>BTC 5m AI Bot</title><style>
body{margin:0;background:#07111f;color:#eef5ff;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.wrap{max-width:900px;margin:auto;padding:14px}.top{display:flex;justify-content:space-between;gap:12px}.pill{padding:10px 14px;border-radius:99px;border:1px solid #2a9d55;background:#173b2a;color:#b7f7ca;font-weight:800}.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:12px;margin-top:14px}.card{grid-column:span 12;background:#10213a;border:1px solid #2a405e;border-radius:18px;padding:14px}.mini{grid-column:span 6}.row{display:flex;justify-content:space-between;gap:12px;padding:8px 0;border-bottom:1px solid rgba(159,178,204,.18)}.muted{color:#9fb2cc}.value{font-size:28px;font-weight:900}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;overflow-wrap:anywhere}a{color:#b9e6ff}@media(max-width:700px){.mini{grid-column:span 12}}
</style></head><body><main class="wrap"><div class="top"><div><h1>Polymarket BTC 5m AI Bot</h1><div class="muted">BTC 5 分鐘市場 · AI 單向 YES / NO · Balanced HF Paper</div></div><div id="status" class="pill">LOADING</div></div><section class="grid">
<article class="card mini"><div class="muted">BTC 即時價格</div><div id="btc" class="value">—</div></article><article class="card mini"><div class="muted">AI 判斷</div><div id="direction" class="value">WAIT</div><div id="prob" class="muted"></div></article>
<article class="card mini"><div class="muted">已實現 PnL</div><div id="pnl" class="value">$0.0000</div></article><article class="card mini"><div class="muted">勝率</div><div id="winrate" class="value">0.00%</div><div id="record" class="muted"></div></article>
<article class="card"><h2>目前 BTC 5 分鐘市場</h2><div class="row"><span>狀態</span><strong id="marketStatus">—</strong></div><div class="row"><span>Slug</span><strong id="slug" class="mono">—</strong></div><div class="row"><span>問題</span><strong id="question" class="mono">—</strong></div></article>
<article class="card"><h2>Balanced HF Profile</h2><div id="profile"></div></article>
<article class="card"><h2>5 分鐘獨立績效序列</h2><div id="history"></div><div class="muted">舊資料庫保留，但不再混入這個 5 分鐘 Balanced HF 報表。</div></article>
<article class="card"><div class="mono"><a href="/ai/status">/ai/status</a> · <a href="/risk/profile">/risk/profile</a> · <a href="/history/status">/history/status</a> · <a href="/monitor">/monitor</a> · <a href="/diagnostics">/diagnostics</a> · <a href="/performance">/performance</a></div></article>
</section></main><script>
const $=id=>document.getElementById(id);const pct=x=>Number.isFinite(Number(x))?`${(Number(x)*100).toFixed(2)}%`:'—';const usd=x=>Number.isFinite(Number(x))?`$${Number(x).toFixed(4)}`:'—';const row=(a,b)=>`<div class="row"><span>${a}</span><strong>${b}</strong></div>`;async function refresh(){const [s,a,h]=await Promise.all([fetch('/state',{cache:'no-store'}).then(r=>r.json()),fetch('/ai/status',{cache:'no-store'}).then(r=>r.json()),fetch('/history/status',{cache:'no-store'}).then(r=>r.json())]);const p=s.paper_portfolio||{},sum=p.summary||{},m=s.current_market||{},prices=s.btc_prices_tail||[],last=prices.length?prices[prices.length-1]:null;const ai=a.ai||{},r=a.risk_profile||{},db=h.database||{};$('status').textContent=s.market_discovery_status==='ready'?'SYSTEM ACTIVE':'WAITING';$('btc').textContent=last?`$${Number(last[1]).toLocaleString(undefined,{maximumFractionDigits:2})}`:'—';$('direction').textContent=ai.direction||'WAIT';$('prob').textContent=`上漲機率 ${pct(ai.fair_probability_up)} · 信心 ${pct(ai.confidence)}`;$('pnl').textContent=usd(sum.realized_pnl||0);$('winrate').textContent=pct(sum.win_rate||0);$('record').textContent=`勝 ${sum.wins||0} · 負 ${sum.losses||0} · 平 ${sum.flat||0}`;$('marketStatus').textContent=s.market_discovery_status||'—';$('slug').textContent=m.slug||'—';$('question').textContent=m.question||'—';$('profile').innerHTML=row('單筆權益比例',pct(r.effective_max_order_equity_fraction))+row('日損線',pct(r.effective_max_daily_loss_fraction))+row('總曝險',usd(r.effective_max_open_notional_usd))+row('冷卻',`${r.signal_cooldown_ms||0} ms`)+row('策略評估',`${r.strategy_evaluation_interval_ms||0} ms`)+row('Workers',r.execution_workers||0)+row('Queue',r.max_queue_size||0);$('history').innerHTML=row('Profile',h.profile||'—')+row('隔離資料庫',db.is_btc5m_isolated?'ON':'OFF')+row('目前檔案',db.filename||'—')+row('舊資料庫',db.legacy_database_preserved||'—')}refresh();setInterval(refresh,2000)
</script></body></html>
"""


def register_dashboard5m(app: FastAPI) -> None:
    @app.middleware("http")
    async def redirect_legacy_root(request: Request, call_next: Any) -> Any:
        if request.method == "GET" and request.url.path == "/":
            return RedirectResponse(url="/dashboard5m", status_code=307)
        return await call_next(request)

    @app.get("/dashboard5m", response_class=HTMLResponse)
    async def dashboard5m() -> HTMLResponse:
        return HTMLResponse(HTML)
