from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse


EVALUATION_HTML = r"""
<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>BTC 5m Evaluation</title><style>
body{margin:0;background:#07111f;color:#eef5ff;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.wrap{max-width:900px;margin:auto;padding:14px}.card{background:#10213a;border:1px solid #2a405e;border-radius:18px;padding:14px;margin-top:12px}.row{display:flex;justify-content:space-between;gap:12px;padding:8px 0;border-bottom:1px solid rgba(159,178,204,.18)}.muted{color:#9fb2cc}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;overflow-wrap:anywhere}a{color:#b9e6ff}
</style></head><body><main class="wrap"><h1>BTC 5m Balanced HF 評估</h1><p class="muted">新 5 分鐘策略與舊歷史分開統計，不會合併。</p><section class="card"><h2>樣本階段</h2><div id="stage"></div><p id="recommendation" class="muted"></p></section><section class="card"><h2>新 5 分鐘序列</h2><div id="current"></div></section><section class="card"><h2>舊歷史序列</h2><div id="legacy"></div></section><section class="card"><div class="mono"><a href="/dashboard5m">/dashboard5m</a> · <a href="/evaluation/status">/evaluation/status</a> · <a href="/performance/compare">/performance/compare</a> · <a href="/history/status">/history/status</a></div></section></main><script>
const $=id=>document.getElementById(id);const pct=x=>Number.isFinite(Number(x))?`${(Number(x)*100).toFixed(2)}%`:'—';const usd=x=>Number.isFinite(Number(x))?`$${Number(x).toFixed(4)}`:'—';const row=(a,b)=>`<div class="row"><span>${a}</span><strong>${b}</strong></div>`;const summary=x=>row('資料庫',x.db_path||'—')+row('已平倉',x.closed_trades||0)+row('勝率',pct(x.win_rate||0))+row('淨 PnL',usd(x.net_pnl||0))+row('Profit Factor',x.profit_factor??'—');async function refresh(){const [e,c]=await Promise.all([fetch('/evaluation/status',{cache:'no-store'}).then(r=>r.json()),fetch('/performance/compare',{cache:'no-store'}).then(r=>r.json())]);$('stage').innerHTML=row('Profile',e.profile||'—')+row('階段',e.stage||'—')+row('最低樣本',e.minimum_initial_sample||0)+row('建議複核樣本',e.preferred_review_sample||0);$('recommendation').textContent=e.recommendation||'';$('current').innerHTML=summary(c.current||{});$('legacy').innerHTML=summary(c.legacy_15m_history||{})}refresh();setInterval(refresh,3000)</script></body></html>
"""


def _empty_summary(path: str, exists: bool) -> dict[str, Any]:
    return {
        "db_path": path,
        "exists": exists,
        "closed_trades": 0,
        "wins": 0,
        "losses": 0,
        "flat": 0,
        "win_rate": 0.0,
        "net_pnl": 0.0,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
        "profit_factor": None,
    }


def _read_summary(path: str) -> dict[str, Any]:
    file = Path(path)
    if not file.exists():
        return _empty_summary(path, False)
    try:
        with sqlite3.connect(path, timeout=2) as db:
            row = db.execute(
                """
                SELECT
                    COUNT(*) AS closed_trades,
                    COALESCE(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END), 0) AS wins,
                    COALESCE(SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END), 0) AS losses,
                    COALESCE(SUM(CASE WHEN realized_pnl = 0 THEN 1 ELSE 0 END), 0) AS flat,
                    COALESCE(SUM(realized_pnl), 0) AS net_pnl,
                    COALESCE(SUM(CASE WHEN realized_pnl > 0 THEN realized_pnl ELSE 0 END), 0) AS gross_profit,
                    ABS(COALESCE(SUM(CASE WHEN realized_pnl < 0 THEN realized_pnl ELSE 0 END), 0)) AS gross_loss
                FROM paper_trades
                """
            ).fetchone()
    except (sqlite3.Error, OSError):
        return _empty_summary(path, True)
    if row is None:
        return _empty_summary(path, True)
    closed = int(row[0] or 0)
    wins = int(row[1] or 0)
    losses = int(row[2] or 0)
    flat = int(row[3] or 0)
    net = float(row[4] or 0.0)
    gross_profit = float(row[5] or 0.0)
    gross_loss = float(row[6] or 0.0)
    return {
        "db_path": path,
        "exists": True,
        "closed_trades": closed,
        "wins": wins,
        "losses": losses,
        "flat": flat,
        "win_rate": round(wins / max(1, wins + losses), 6),
        "net_pnl": round(net, 8),
        "gross_profit": round(gross_profit, 8),
        "gross_loss": round(gross_loss, 8),
        "profit_factor": round(gross_profit / gross_loss, 6) if gross_loss > 0 else None,
    }


def register_evaluation_routes(app: FastAPI, portfolio: Any) -> None:
    legacy_path = "/data/polymarket_paper.db"

    @app.get("/evaluation", response_class=HTMLResponse)
    async def evaluation_dashboard() -> HTMLResponse:
        return HTMLResponse(EVALUATION_HTML)

    @app.get("/performance/compare")
    async def performance_compare() -> dict[str, Any]:
        current = _read_summary(str(portfolio.store.db_path))
        legacy = _read_summary(legacy_path)
        return {
            "current_profile": "balanced_btc5m_hf",
            "current": current,
            "legacy_15m_history": legacy,
            "note": "The two databases are reported separately and are never merged.",
        }

    @app.get("/evaluation/status")
    async def evaluation_status() -> dict[str, Any]:
        current = _read_summary(str(portfolio.store.db_path))
        closed = int(current["closed_trades"])
        if closed < 100:
            stage = "collecting_initial_sample"
            recommendation = "Keep Paper mode running. Do not judge the strategy yet."
        elif closed < 500:
            stage = "early_evaluation"
            recommendation = "Review win rate, profit factor, drawdown and exit reasons before tuning again."
        else:
            stage = "evaluation_ready"
            recommendation = "Use the isolated 5m report for parameter review. Live mode should remain disabled until manual validation is complete."
        return {
            "profile": "balanced_btc5m_hf",
            "stage": stage,
            "minimum_initial_sample": 100,
            "preferred_review_sample": 500,
            "current": current,
            "recommendation": recommendation,
        }
