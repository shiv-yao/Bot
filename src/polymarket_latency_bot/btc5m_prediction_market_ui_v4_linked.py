from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from .btc5m_prediction_market_ui_v4 import DASHBOARD_HTML_V4


UI_BUILD = "btc5m-v4-poly-integrations-20260608-1"
_STATUS_FETCH = "const [s,m]=await Promise.all([fetch('/status',{cache:'no-store'}).then(r=>r.json()),fetch('/mode',{cache:'no-store'}).then(r=>r.json())]);"
_STATUS_FETCH_ROBUST = "const statusResponse=await fetch('/status',{cache:'no-store'});if(!statusResponse.ok)throw new Error('status_unavailable');const s=await statusResponse.json();let m={};try{const modeResponse=await fetch('/mode',{cache:'no-store'});if(modeResponse.ok)m=await modeResponse.json()}catch(_){m={}};refreshFailures=0;lastSuccessAt=Date.now();"
_CATCH_OFFLINE = "}catch(e){$('system').textContent='OFFLINE';$('system').className='pill no'}}"
_CATCH_ROBUST = "}catch(e){refreshFailures+=1;const age=lastSuccessAt?Math.max(0,Date.now()-lastSuccessAt):null;if($('lastSuccessAge'))$('lastSuccessAge').textContent=age===null?'—':`${Math.round(age/1000)} 秒前`;if($('freshnessStatus'))$('freshnessStatus').textContent=refreshFailures>=3?'OFFLINE':'RECONNECTING';$('system').textContent=refreshFailures>=3?'OFFLINE':'RECONNECTING';$('system').className=`pill ${refreshFailures>=3?'no':'wait'}`}}"
_SOURCE_HEALTH_CARD = rf'''<article class="card mini"><h3>即時資料健康</h3><div class="row"><span>資料狀態</span><strong id="freshnessStatus">—</strong></div><div class="row"><span>最後成功更新</span><strong id="lastSuccessAge">—</strong></div><div class="row"><span>Connected Sources</span><strong id="connectedSources">—</strong></div><div class="row"><span>Clean Fusion Sources</span><strong id="fusionCleanSources">—</strong></div><div class="row"><span>Fusion Status</span><strong id="fusionStatus">—</strong></div><div class="row"><span>最舊盤口資料</span><strong id="oldestBookAge">—</strong></div><div class="row"><span>來源摘要</span><strong id="sourceSummary" class="mono">—</strong></div><div class="row"><span>UI Build</span><strong id="uiBuild" class="mono">{UI_BUILD}</strong></div></article>'''
_ACTIVE_BLOCK = "const active=market.discovery_status==='ready';$('system').textContent=active?'SYSTEM ACTIVE':'WAITING';$('system').className=`pill ${active?'yes':'wait'}`;$('updated').textContent=`更新時間 ${new Date().toLocaleTimeString()}`"
_HEALTH_BLOCK = r"""const sources=s.sources||{},sourceRows=Object.entries(sources),connectedCount=sourceRows.filter(([,row])=>row?.connected===true).length,sourceSummary=sourceRows.map(([name,row])=>`${name}:${row?.connected===true?'UP':'DOWN'}`).join(', ')||'—',fusion=s.fusion||{},fusionState=String(fusion.status||'waiting_for_sources'),cleanSources=Number(fusion.clean_source_count??fusion.source_count??0),bookAges=[market.yes_book_age_ms,market.no_book_age_ms].filter(x=>Number.isFinite(Number(x))).map(Number),oldestBookAge=bookAges.length?Math.max(...bookAges):null,stale=oldestBookAge!==null&&oldestBookAge>5000,degraded=market.discovery_status!=='ready'||fusionState!=='ready'||cleanSources<2||connectedCount<2;const liveState=stale?'STALE DATA':degraded?'DEGRADED':'SYSTEM ACTIVE';$('freshnessStatus').textContent=liveState;$('lastSuccessAge').textContent='剛剛';$('connectedSources').textContent=connectedCount;$('fusionCleanSources').textContent=cleanSources;$('fusionStatus').textContent=fusionState;$('oldestBookAge').textContent=ms(oldestBookAge);$('sourceSummary').textContent=sourceSummary;$('system').textContent=liveState;$('system').className=`pill ${liveState==='SYSTEM ACTIVE'?'yes':liveState==='STALE DATA'?'no':'wait'}`;$('updated').textContent=`更新時間 ${new Date().toLocaleTimeString()}`"""
_NO_STORE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


def build_dashboard_html_v4() -> str:
    """Add health and integration shortcuts without rewriting the base dashboard."""

    html = DASHBOARD_HTML_V4
    marker = '<a href="/docs">/docs</a>'
    shortcuts = (
        '<a href="/selfcheck">/selfcheck</a>'
        '<a href="/runtime-health">/runtime-health</a>'
        '<a href="/storage-health">/storage-health</a>'
        '<a href="/integrations">/integrations</a>'
        '<a href="/integrations/poly-data">/poly-data</a>'
        '<a href="/integrations/poly-maker-shadow">/poly-maker-shadow</a>'
    )
    for href in (
        '/selfcheck',
        '/runtime-health',
        '/storage-health',
        '/integrations',
        '/integrations/poly-data',
        '/integrations/poly-maker-shadow',
    ):
        label = href if href not in {'/integrations/poly-data', '/integrations/poly-maker-shadow'} else ('/poly-data' if href.endswith('poly-data') else '/poly-maker-shadow')
        link = f'<a href="{href}">{label}</a>'
        if link not in html:
            html = html.replace(marker, f'{link}{marker}')
    if 'id="freshnessStatus"' not in html:
        html = html.replace('<article class="card"><a href="/mode">', f'{_SOURCE_HEALTH_CARD}<article class="card"><a href="/mode">')
    if "let refreshFailures=0,lastSuccessAt=0;" not in html:
        html = html.replace("async function refresh(){try{", "let refreshFailures=0,lastSuccessAt=0;\nasync function refresh(){try{")
    html = html.replace(_STATUS_FETCH, _STATUS_FETCH_ROBUST)
    html = html.replace(_ACTIVE_BLOCK, _HEALTH_BLOCK)
    html = html.replace(_CATCH_OFFLINE, _CATCH_ROBUST)
    return html


def register_btc5m_prediction_market_ui_v4(app: FastAPI) -> None:
    @app.get("/ui", response_class=HTMLResponse)
    async def dedicated_ui() -> HTMLResponse:
        return HTMLResponse(build_dashboard_html_v4(), headers=_NO_STORE_HEADERS)
