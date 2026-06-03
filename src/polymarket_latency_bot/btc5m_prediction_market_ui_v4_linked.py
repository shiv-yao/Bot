from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from .btc5m_prediction_market_ui_v4 import DASHBOARD_HTML_V4


_STATUS_FETCH = "const [s,m]=await Promise.all([fetch('/status',{cache:'no-store'}).then(r=>r.json()),fetch('/mode',{cache:'no-store'}).then(r=>r.json())]);"
_STATUS_FETCH_ROBUST = "const statusResponse=await fetch('/status',{cache:'no-store'});if(!statusResponse.ok)throw new Error('status_unavailable');const s=await statusResponse.json();let m={};try{const modeResponse=await fetch('/mode',{cache:'no-store'});if(modeResponse.ok)m=await modeResponse.json()}catch(_){m={}};refreshFailures=0;"
_CATCH_OFFLINE = "}catch(e){$('system').textContent='OFFLINE';$('system').className='pill no'}}"
_CATCH_ROBUST = "}catch(e){refreshFailures+=1;$('system').textContent=refreshFailures>=3?'OFFLINE':'RECONNECTING';$('system').className=`pill ${refreshFailures>=3?'no':'wait'}`}}"


def build_dashboard_html_v4() -> str:
    """Insert selfcheck and make the live status indicator tolerant of brief API jitter."""

    html = DASHBOARD_HTML_V4
    marker = '<a href="/docs">/docs</a>'
    shortcut = '<a href="/selfcheck">/selfcheck</a>'
    if shortcut not in html:
        html = html.replace(marker, f"{shortcut}{marker}")
    if "let refreshFailures=0;" not in html:
        html = html.replace("async function refresh(){try{", "let refreshFailures=0;\nasync function refresh(){try{")
    html = html.replace(_STATUS_FETCH, _STATUS_FETCH_ROBUST)
    html = html.replace(_CATCH_OFFLINE, _CATCH_ROBUST)
    return html


def register_btc5m_prediction_market_ui_v4(app: FastAPI) -> None:
    @app.get("/ui", response_class=HTMLResponse)
    async def dedicated_ui() -> HTMLResponse:
        return HTMLResponse(build_dashboard_html_v4())
