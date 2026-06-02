from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from .btc5m_prediction_market_ui_v4 import DASHBOARD_HTML_V4


def build_dashboard_html_v4() -> str:
    """Insert the read-only selfcheck shortcut without rewriting the base dashboard."""

    marker = '<a href="/docs">/docs</a>'
    shortcut = '<a href="/selfcheck">/selfcheck</a>'
    if shortcut in DASHBOARD_HTML_V4:
        return DASHBOARD_HTML_V4
    return DASHBOARD_HTML_V4.replace(marker, f"{shortcut}{marker}")


def register_btc5m_prediction_market_ui_v4(app: FastAPI) -> None:
    @app.get("/ui", response_class=HTMLResponse)
    async def dedicated_ui() -> HTMLResponse:
        return HTMLResponse(build_dashboard_html_v4())
