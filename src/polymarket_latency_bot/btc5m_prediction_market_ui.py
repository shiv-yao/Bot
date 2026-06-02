from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse


DASHBOARD_HTML = r"""
<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <title>Polymarket BTC 5m Prediction Market Scale In</title>
  <style>
    :root{color-scheme:dark;--bg:#07111f;--panel:#10213a;--line:#2a405e;--text:#eef5ff;--muted:#9fb2cc;--yes:#2ed573;--no:#ff6b6b;--wait:#f6c344;--blue:#52b7ff}
    *{box-sizing:border-box}body{margin:0;background:linear-gradient(180deg,#07111f,#091729 48%,#07111f);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.wrap{max-width:980px;margin:auto;padding:16px 14px 34px}.top{display:flex;justify-content:space-between;gap:12px}.eyebrow{font-size:12px;color:var(--muted);letter-spacing:.14em;text-transform:uppercase}.title{font-size:25px;font-weight:900;margin:4px 0}.sub{color:var(--muted);font-size:14px}.pill{border:1px solid var(--line);padding:8px 12px;border-radius:999px;font-weight:800;font-size:12px;background:#0c1b31;white-space:nowrap}.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:12px;margin-top:14px}.card{grid-column:span 12;background:rgba(16,33,58,.96);border:1px solid var(--line);border-radius:18px;padding:14px;box-shadow:0 10px 30px rgba(0,0,0,.16)}.mini{grid-column:span 6}.third{grid-column:span 4}.quarter{grid-column:span 3}.label{color:var(--muted);font-size:13px}.value{font-size:31px;font-weight:900;margin-top:4px}.smallvalue{font-size:21px;font-weight:850;margin-top:4px}.row{display:flex;justify-content:space-between;gap:12px;padding:9px 0;border-bottom:1px solid rgba(159,178,204,.16)}.row:last-child{border-bottom:0}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;overflow-wrap:anywhere}.yes{color:var(--yes)}.no{color:var(--no)}.wait{color:var(--wait)}.muted{color:var(--muted)}.source{display:grid;grid-template-columns:1.3fr .7fr .7fr;gap:8px;padding:9px 0;border-bottom:1px solid rgba(159,178,204,.16)}.links a{color:#b9e6ff;text-decoration:none;margin-right:10px;display:inline-block;margin-top:8px}.stamp{color:var(--muted);font-size:12px;text-align:right;margin-top:10px}@media(max-width:720px){.mini,.third,.quarter{grid-column:span 12}.title{font-size:21px}.value{font-size:27px}}
  </style>
</head>
<body>
<main class="wrap">
<header class="top"><div><div class="eyebrow">Polymarket · BTC 5 Minute</div><div class="title">BTC 5 分鐘預測市場</div><div class="sub">Paper 模式分批加倉：第 1 次 50% · 第 2 次 30% · 第 3 次 20%</div></div><div id="systemPill" class="pill">LOADING</div></header>
<section class="grid">
  <article class="card mini"><div class="label">本輪 AI 判斷</div><div id="direction" class="value wait">WAIT</div><div id="reason" class="muted">等待資料同步</div></article>
  <article class="card mini"><div class="label">距離本輪結束</div><div id="countdown" class="value">--:--</div><div class="muted">五分鐘結束後自動結算</div></article>
  <article class="card third"><div class="label">BTC 上漲機率</div><div id="probability" class="smallvalue">—</div></article>
  <article class="card third"><div class="label">AI 信心</div><div id="confidence" class="smallvalue">—</div></article>
  <article class="card third"><div class="label">本輪狀態</div><div id="roundStatus" class="smallvalue">—</div></article>
  <article class="card quarter"><div class="label">勝率</div><div id="winRate" class="smallvalue">0.00%</div></article>
  <article class="card quarter"><div class="label">勝 / 負 / 平</div><div id="record" class="smallvalue">0 / 0 / 0</div></article>
  <article class="card quarter"><div class="label">WAIT 跳過</div><div id="skipped" class="smallvalue">0</div></article>
  <article class="card quarter"><div class="label">已實現 PnL</div><div id="pnl" class="smallvalue">$0.0000</div></article>
  <article class="card mini"><h3>目前市場</h3><div class="row"><span>市場狀態</span><strong id="marketStatus">—</strong></div><div class="row"><span>Slug</span><strong id="slug" class="mono">—</strong></div><div class="row"><span>問題</span><strong id="question" class="mono">—</strong></div><div class="row"><span>YES Ask</span><strong id="yesAsk">—</strong></div><div class="row"><span>NO Ask</span><strong id="noAsk">—</strong></div></article>
  <article class="card mini"><h3>本輪預測明細</h3><div class="row"><span>BTC 開盤價</span><strong id="btcOpen">—</strong></div><div class="row"><span>BTC 收盤價</span><strong id="btcClose">—</strong></div><div class="row"><span>合約進場價</span><strong id="entryPrice">—</strong></div><div class="row"><span>模擬本金</span><strong id="notional">—</strong></div><div class="row"><span>已完成加倉</span><strong id="scaleCount">0 / 3</strong></div><div class="row"><span>下一次加倉</span><strong id="nextScale">第 1 次 · 50%</strong></div><div class="row"><span>結算結果</span><strong id="outcome">—</strong></div></article>
  <article class="card mini"><h3>分批加倉規則</h3><div class="row"><span>第 1 次</span><strong>50%</strong></div><div class="row"><span>第 2 次</span><strong>30%</strong></div><div class="row"><span>第 3 次</span><strong>20%</strong></div><div class="row"><span>每輪最多</span><strong>3 筆</strong></div><div class="row"><span>方向翻轉</span><strong>停止追加</strong></div></article>
  <article class="card mini"><h3>安全模式</h3><div class="row"><span>執行模式</span><strong id="execution">—</strong></div><div class="row"><span>Paper 預測</span><strong id="paperEnabled">ON</strong></div><div class="row"><span>真實下單</span><strong id="liveOrders">OFF</strong></div><div class="row"><span>錢包簽名</span><strong id="wallet">OFF</strong></div><div class="row"><span>Live Trading</span><strong id="liveTrading">OFF</strong></div></article>
  <article class="card mini"><h3>資料來源</h3><div id="sources"><div class="muted">尚未取得來源狀態</div></div></article>
  <article class="card mini"><h3>最近已結算輪次</h3><pre id="closedRounds" class="mono">[]</pre></article>
  <article class="card"><div class="links"><a href="/mode">/mode</a><a href="/status">/status</a><a href="/paper/winrate">/paper/winrate</a><a href="/paper/rounds">/paper/rounds</a><a href="/healthz">/healthz</a><a href="/docs">/docs</a></div><div id="updated" class="stamp">尚未更新</div></article>
</section>
</main>
<script>
const $=id=>document.getElementById(id);const pct=x=>Number.isFinite(Number(x))?`${(Number(x)*100).toFixed(2)}%`:'—';const price=x=>Number.isFinite(Number(x))?Number(x).toFixed(2):'—';const contract=x=>Number.isFinite(Number(x))?Number(x).toFixed(4):'—';const usd=x=>Number.isFinite(Number(x))?`$${Number(x).toFixed(4)}`:'—';function klass(d){return d==='YES'?'yes':d==='NO'?'no':'wait'}function countdown(c){const now=Math.floor(Date.now()/1000);let start=Number(c?.interval_start||0);if(!start){const m=String(c?.slug||'').match(/(\d{10})$/);if(m)start=Number(m[1])}if(!start)return'--:--';const r=Math.max(0,start+300-now);return`${String(Math.floor(r/60)).padStart(2,'0')}:${String(r%60).padStart(2,'0')}`}function sourceRows(s){const e=Object.entries(s||{});if(!e.length)return'<div class="muted">尚未取得來源狀態</div>';return e.map(([n,v])=>`<div class="source"><strong>${n}</strong><span class="${v.connected?'yes':'no'}">${v.connected?'ONLINE':'OFFLINE'}</span><span>${Number.isFinite(Number(v.age_ms))?Math.round(Number(v.age_ms))+' ms':'—'}</span></div>`).join('')}function nextScaleLabel(round){const count=Number(round.order_count||0);if(count>=3)return'已完成';const weights=['50%','30%','20%'];return`第 ${count+1} 次 · ${weights[count]}`}async function refresh(){try{const [s,m]=await Promise.all([fetch('/status',{cache:'no-store'}).then(r=>r.json()),fetch('/mode',{cache:'no-store'}).then(r=>r.json())]);const paper=s.paper||{},sum=paper.summary||{},round=paper.current_round||{},market=s.market||{},current=market.current||{},ai=s.ai||{},safe=m.safety||{};const d=round.direction||ai.direction||'WAIT';$('direction').textContent=d;$('direction').className=`value ${klass(d)}`;$('reason').textContent=round.reason||ai.reason||'等待資料同步';$('countdown').textContent=countdown(current);$('probability').textContent=pct(ai.probability_up);$('confidence').textContent=pct(ai.confidence);$('roundStatus').textContent=round.status||'waiting';$('winRate').textContent=pct(sum.win_rate||0);$('record').textContent=`${sum.wins||0} / ${sum.losses||0} / ${sum.flat||0}`;$('skipped').textContent=sum.skipped_wait||0;$('pnl').textContent=usd(sum.realized_pnl||0);$('marketStatus').textContent=market.discovery_status||'—';$('slug').textContent=current.slug||'—';$('question').textContent=current.question||'—';$('yesAsk').textContent=contract(market.yes_ask);$('noAsk').textContent=contract(market.no_ask);$('btcOpen').textContent=price(round.btc_open);$('btcClose').textContent=price(round.btc_close);$('entryPrice').textContent=contract(round.entry_price);$('notional').textContent=usd(round.notional_usd);$('scaleCount').textContent=`${Number(round.order_count||0)} / 3`;$('nextScale').textContent=nextScaleLabel(round);$('outcome').textContent=round.outcome||'—';$('execution').textContent=m.execution||'—';$('paperEnabled').textContent=safe.paper_predictions_enabled?'ON':'OFF';$('liveOrders').textContent=safe.live_orders_enabled?'ON':'OFF';$('wallet').textContent=safe.wallet_signing_enabled?'ON':'OFF';$('liveTrading').textContent=safe.live_trading_enabled?'ON':'OFF';$('sources').innerHTML=sourceRows(s.sources);$('closedRounds').textContent=JSON.stringify((paper.closed_trades||[]).slice(0,5),null,2);const active=market.discovery_status==='ready';$('systemPill').textContent=active?'SYSTEM ACTIVE':'WAITING';$('systemPill').className=`pill ${active?'yes':'wait'}`;$('updated').textContent=`更新時間 ${new Date().toLocaleTimeString()}`}catch(e){$('systemPill').textContent='OFFLINE';$('systemPill').className='pill no';$('updated').textContent=`讀取失敗 ${new Date().toLocaleTimeString()}`}}refresh();setInterval(refresh,1500)
</script>
</body></html>
"""


def register_btc5m_prediction_market_ui(app: FastAPI) -> None:
    @app.get("/ui", response_class=HTMLResponse)
    async def dedicated_ui() -> HTMLResponse:
        return HTMLResponse(DASHBOARD_HTML)
