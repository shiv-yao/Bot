from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse


DASHBOARD_HTML = r"""
<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <title>BTC 5m Event Prediction</title>
  <style>
    :root{color-scheme:dark;--bg:#07111f;--panel:#10213a;--line:#2a405e;--text:#eef5ff;--muted:#9fb2cc;--good:#2ed573;--bad:#ff6b6b;--wait:#f6c344;--blue:#52b7ff}
    *{box-sizing:border-box}body{margin:0;background:linear-gradient(180deg,#07111f,#091729 48%,#07111f);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.wrap{max-width:980px;margin:auto;padding:16px 14px 32px}.top{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.eyebrow{font-size:12px;color:var(--muted);letter-spacing:.14em;text-transform:uppercase}.title{font-size:26px;font-weight:900;margin:4px 0}.sub{color:var(--muted);font-size:14px}.pill{border:1px solid var(--line);padding:8px 12px;border-radius:999px;font-weight:800;font-size:12px;background:#0c1b31;white-space:nowrap}.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:12px;margin-top:14px}.card{grid-column:span 12;background:rgba(16,33,58,.95);border:1px solid var(--line);border-radius:18px;padding:14px;box-shadow:0 10px 30px rgba(0,0,0,.16)}.mini{grid-column:span 6}.third{grid-column:span 4}.label{color:var(--muted);font-size:13px}.value{font-size:32px;font-weight:900;margin-top:4px}.smallvalue{font-size:22px;font-weight:850;margin-top:4px}.row{display:flex;justify-content:space-between;gap:12px;padding:9px 0;border-bottom:1px solid rgba(159,178,204,.16)}.row:last-child{border-bottom:0}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;overflow-wrap:anywhere}.source{display:grid;grid-template-columns:1.3fr .7fr .7fr;gap:8px;padding:9px 0;border-bottom:1px solid rgba(159,178,204,.16)}.good{color:var(--good)}.bad{color:var(--bad)}.wait{color:var(--wait)}.blue{color:var(--blue)}.bar{height:8px;background:#0b1728;border-radius:999px;overflow:hidden;margin-top:10px}.fill{height:100%;width:50%;background:linear-gradient(90deg,#2ed573,#52b7ff);transition:width .3s ease}.links a{color:#b9e6ff;text-decoration:none;margin-right:10px;display:inline-block;margin-top:8px}.hint{color:var(--muted);font-size:12px;margin-top:6px}.stamp{color:var(--muted);font-size:12px;text-align:right;margin-top:10px}@media(max-width:700px){.mini,.third{grid-column:span 12}.top{align-items:flex-start}.title{font-size:22px}.source{grid-template-columns:1fr .7fr .7fr}.value{font-size:28px}}
  </style>
</head>
<body>
<main class="wrap">
  <header class="top">
    <div>
      <div class="eyebrow">Polymarket · BTC 5 Minute</div>
      <div class="title">Event Prediction 專用儀表板</div>
      <div class="sub">只判斷 BTC 五分鐘後漲跌 · YES / NO / WAIT · Prediction only</div>
    </div>
    <div id="systemPill" class="pill">LOADING</div>
  </header>

  <section class="grid">
    <article class="card mini">
      <div class="label">AI 判斷</div>
      <div id="direction" class="value wait">WAIT</div>
      <div id="decisionHint" class="hint">等待市場與資料來源同步</div>
    </article>
    <article class="card mini">
      <div class="label">距離本輪結束</div>
      <div id="countdown" class="value">--:--</div>
      <div class="hint">依目前 5 分鐘市場時間窗估算</div>
    </article>

    <article class="card third">
      <div class="label">上漲機率</div>
      <div id="probability" class="smallvalue">—</div>
      <div class="bar"><div id="probabilityBar" class="fill"></div></div>
    </article>
    <article class="card third">
      <div class="label">AI 信心</div>
      <div id="confidence" class="smallvalue">—</div>
      <div class="hint">低於門檻時維持 WAIT</div>
    </article>
    <article class="card third">
      <div class="label">最佳 Edge</div>
      <div id="selectedEdge" class="smallvalue">—</div>
      <div class="hint">低於門檻時維持 WAIT</div>
    </article>

    <article class="card mini">
      <h3>盤面價格</h3>
      <div class="row"><span>YES Ask</span><strong id="yesAsk">—</strong></div>
      <div class="row"><span>NO Ask</span><strong id="noAsk">—</strong></div>
      <div class="row"><span>YES Edge</span><strong id="yesEdge">—</strong></div>
      <div class="row"><span>NO Edge</span><strong id="noEdge">—</strong></div>
    </article>
    <article class="card mini">
      <h3>目前市場</h3>
      <div class="row"><span>發現狀態</span><strong id="discovery">—</strong></div>
      <div class="row"><span>Slug</span><strong id="slug" class="mono">—</strong></div>
      <div class="row"><span>問題</span><strong id="question" class="mono">—</strong></div>
    </article>

    <article class="card mini">
      <h3>資料來源</h3>
      <div id="sources"><div class="hint">尚未取得來源狀態</div></div>
    </article>
    <article class="card mini">
      <h3>系統安全模式</h3>
      <div class="row"><span>執行模式</span><strong id="execution">prediction_only</strong></div>
      <div class="row"><span>下單</span><strong id="orders">OFF</strong></div>
      <div class="row"><span>持倉模擬</span><strong id="positions">OFF</strong></div>
      <div class="row"><span>錢包簽名</span><strong id="wallet">OFF</strong></div>
      <div class="row"><span>一般事件掃描</span><strong id="scanner">OFF</strong></div>
    </article>

    <article class="card">
      <h3>Fusion 狀態</h3>
      <pre id="fusion" class="mono">{}</pre>
      <div class="links"><a href="/status">/status</a><a href="/mode">/mode</a><a href="/healthz">/healthz</a><a href="/docs">/docs</a></div>
      <div id="updated" class="stamp">尚未更新</div>
    </article>
  </section>
</main>
<script>
const $=id=>document.getElementById(id);
const pct=x=>Number.isFinite(Number(x))?`${(Number(x)*100).toFixed(2)}%`:'—';
const price=x=>Number.isFinite(Number(x))?Number(x).toFixed(4):'—';
const boolText=x=>x?'ON':'OFF';
function directionClass(direction){return direction==='YES'?'good':direction==='NO'?'bad':'wait'}
function calcCountdown(current){const now=Math.floor(Date.now()/1000);let start=Number(current?.interval_start||0);if(!start){const slug=String(current?.slug||'');const m=slug.match(/(\d{10})$/);if(m)start=Number(m[1])}if(!start)return '--:--';const remain=Math.max(0,start+300-now);return `${String(Math.floor(remain/60)).padStart(2,'0')}:${String(remain%60).padStart(2,'0')}`}
function sourceRows(sources){const entries=Object.entries(sources||{});if(!entries.length)return '<div class="hint">尚未取得來源狀態</div>';return entries.map(([name,s])=>{const ok=!!s.connected;const age=Number.isFinite(Number(s.age_ms))?`${Math.round(Number(s.age_ms))} ms`:'—';return `<div class="source"><strong>${name}</strong><span class="${ok?'good':'bad'}">${ok?'ONLINE':'OFFLINE'}</span><span>${age}</span></div>`}).join('')}
async function refresh(){try{const [status,mode]=await Promise.all([fetch('/status',{cache:'no-store'}).then(r=>r.json()),fetch('/mode',{cache:'no-store'}).then(r=>r.json())]);const ai=status.ai||{},market=status.market||{},current=market.current||{},safe=mode.safety||{};const direction=ai.direction||'WAIT';$('direction').textContent=direction;$('direction').className=`value ${directionClass(direction)}`;$('decisionHint').textContent=direction==='WAIT'?'等待信心或 Edge 達到門檻':`目前偏向 ${direction}`;$('countdown').textContent=calcCountdown(current);$('probability').textContent=pct(ai.probability_up);$('probabilityBar').style.width=`${Math.max(0,Math.min(100,Number(ai.probability_up||0.5)*100))}%`;$('confidence').textContent=pct(ai.confidence);$('selectedEdge').textContent=pct(ai.selected_edge);$('yesAsk').textContent=price(market.yes_ask);$('noAsk').textContent=price(market.no_ask);$('yesEdge').textContent=pct(ai.yes_edge);$('noEdge').textContent=pct(ai.no_edge);$('discovery').textContent=market.discovery_status||'—';$('slug').textContent=current.slug||'—';$('question').textContent=current.question||'—';$('sources').innerHTML=sourceRows(status.sources);$('fusion').textContent=JSON.stringify(status.fusion||{},null,2);$('execution').textContent=mode.execution||'—';$('orders').textContent=boolText(safe.orders_enabled);$('positions').textContent=boolText(safe.paper_positions_enabled);$('wallet').textContent=boolText(safe.wallet_signing_enabled);$('scanner').textContent=boolText(safe.general_event_scanner_enabled);const active=market.discovery_status==='ready';$('systemPill').textContent=active?'SYSTEM ACTIVE':'WAITING';$('systemPill').className=`pill ${active?'good':'wait'}`;$('updated').textContent=`更新時間 ${new Date().toLocaleTimeString()}`}catch(error){$('systemPill').textContent='OFFLINE';$('systemPill').className='pill bad';$('updated').textContent=`讀取失敗 ${new Date().toLocaleTimeString()}`}}
refresh();setInterval(refresh,1500);
</script>
</body>
</html>
"""


def register_btc5m_event_ui(app: FastAPI) -> None:
    @app.get("/ui", response_class=HTMLResponse)
    async def dedicated_ui() -> HTMLResponse:
        return HTMLResponse(DASHBOARD_HTML)
