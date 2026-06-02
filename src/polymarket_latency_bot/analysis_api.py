from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse


REPLAY_HTML = """
<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Trade Replay</title><style>body{margin:0;background:#07111f;color:#eef5ff;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}.wrap{max-width:1000px;margin:auto;padding:14px}.card{background:#10213a;border:1px solid #2a405e;border-radius:18px;padding:14px;margin:10px 0}.row{display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:8px;padding:8px 0;border-bottom:1px solid rgba(159,178,204,.18)}.muted{color:#9fb2cc}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;overflow-wrap:anywhere}a{color:#b9e6ff}@media(max-width:700px){.row{grid-template-columns:1fr 1fr}}</style></head><body><main class="wrap"><h1>Trade Replay</h1><p class="muted">最近已平倉 Paper 交易。</p><div class="card"><div class="mono"><a href="/dashboard5m">/dashboard5m</a> · <a href="/edge-analysis/ui">/edge-analysis/ui</a> · <a href="/replay">/replay</a></div></div><div id="list"></div></main><script>const usd=x=>`$${Number(x||0).toFixed(4)}`;const pct=x=>`${(Number(x||0)*100).toFixed(2)}%`;async function run(){const r=await fetch('/replay?limit=80',{cache:'no-store'}).then(x=>x.json());document.getElementById('list').innerHTML=r.trades.map(t=>`<div class="card"><div class="row"><b>${t.direction}</b><span>${usd(t.realized_pnl)}</span><span>${t.result}</span><span>${t.close_reason}</span></div><div class="row"><span>Entry ${Number(t.entry_price).toFixed(4)}</span><span>Exit ${Number(t.exit_price).toFixed(4)}</span><span>Hold ${t.hold_sec}s</span><span>Orders ${t.order_count||1}</span></div><div class="mono muted">${t.market_slug}<br>${t.token_id}</div></div>`).join('')}run();setInterval(run,5000)</script></body></html>
"""

EDGE_HTML = """
<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Edge Analysis</title><style>body{margin:0;background:#07111f;color:#eef5ff;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}.wrap{max-width:1000px;margin:auto;padding:14px}.card{background:#10213a;border:1px solid #2a405e;border-radius:18px;padding:14px;margin:10px 0}.row{display:flex;justify-content:space-between;gap:10px;padding:8px 0;border-bottom:1px solid rgba(159,178,204,.18)}.muted{color:#9fb2cc}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}a{color:#b9e6ff}</style></head><body><main class="wrap"><h1>Edge Analysis</h1><p class="muted">依方向、進場價區間、出場原因統計勝率與 PnL。</p><div class="card"><div class="mono"><a href="/dashboard5m">/dashboard5m</a> · <a href="/replay/ui">/replay/ui</a> · <a href="/edge-analysis">/edge-analysis</a></div></div><div id="out"></div></main><script>const usd=x=>`$${Number(x||0).toFixed(4)}`;const pct=x=>`${(Number(x||0)*100).toFixed(2)}%`;const section=(title,items)=>`<div class="card"><h2>${title}</h2>${items.map(x=>`<div class="row"><span>${x.bucket}</span><b>${x.trades} 筆 · 勝率 ${pct(x.win_rate)} · PnL ${usd(x.net_pnl)}</b></div>`).join('')}</div>`;async function run(){const r=await fetch('/edge-analysis',{cache:'no-store'}).then(x=>x.json());document.getElementById('out').innerHTML=section('方向',r.by_direction)+section('進場價區間',r.by_entry_price)+section('出場原因',r.by_close_reason)}run();setInterval(run,5000)</script></body></html>
"""


def _connect(path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    return connection


def _load_trades(path: str, limit: int = 500) -> list[dict[str, Any]]:
    if not Path(path).exists():
        return []
    with _connect(path) as db:
        rows = db.execute(
            """
            SELECT payload_json, token_id, direction, notional_usd, entry_price, exit_price,
                   shares, realized_pnl, opened_ms, closed_ms, hold_ms, close_reason, market_slug
            FROM paper_trades
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, min(int(limit), 5000)),),
        ).fetchall()
    trades: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        trade = {key: row[key] for key in row.keys() if key != "payload_json"}
        trade.update(payload)
        pnl = float(trade.get("realized_pnl") or 0.0)
        trade["result"] = "win" if pnl > 1e-9 else "loss" if pnl < -1e-9 else "flat"
        trade["hold_sec"] = round(float(trade.get("hold_ms") or 0) / 1000, 3)
        trades.append(trade)
    return trades


def _price_bucket(price: float) -> str:
    if price < 0.2:
        return "0.00-0.20"
    if price < 0.4:
        return "0.20-0.40"
    if price < 0.6:
        return "0.40-0.60"
    if price < 0.8:
        return "0.60-0.80"
    return "0.80-1.00"


def _aggregate(trades: list[dict[str, Any]], key_fn: Any) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        buckets[str(key_fn(trade))].append(trade)
    result: list[dict[str, Any]] = []
    for bucket, items in buckets.items():
        wins = sum(1 for item in items if float(item.get("realized_pnl") or 0.0) > 1e-9)
        losses = sum(1 for item in items if float(item.get("realized_pnl") or 0.0) < -1e-9)
        net = sum(float(item.get("realized_pnl") or 0.0) for item in items)
        result.append({
            "bucket": bucket,
            "trades": len(items),
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / max(1, wins + losses), 6),
            "net_pnl": round(net, 8),
            "average_pnl": round(net / max(1, len(items)), 8),
        })
    return sorted(result, key=lambda item: (-item["trades"], item["bucket"]))


def register_analysis_routes(app: FastAPI, portfolio: Any) -> None:
    @app.get("/replay")
    async def replay(limit: int = Query(default=100, ge=1, le=1000)) -> dict[str, Any]:
        trades = _load_trades(str(portfolio.store.db_path), limit)
        return {"database": str(portfolio.store.db_path), "count": len(trades), "trades": trades}

    @app.get("/replay/ui", response_class=HTMLResponse)
    async def replay_ui() -> HTMLResponse:
        return HTMLResponse(REPLAY_HTML)

    @app.get("/edge-analysis")
    async def edge_analysis(limit: int = Query(default=1000, ge=1, le=5000)) -> dict[str, Any]:
        trades = _load_trades(str(portfolio.store.db_path), limit)
        return {
            "database": str(portfolio.store.db_path),
            "sample_size": len(trades),
            "by_direction": _aggregate(trades, lambda item: item.get("direction") or "UNKNOWN"),
            "by_entry_price": _aggregate(trades, lambda item: _price_bucket(float(item.get("entry_price") or 0.0))),
            "by_close_reason": _aggregate(trades, lambda item: item.get("close_reason") or "unknown"),
        }

    @app.get("/edge-analysis/ui", response_class=HTMLResponse)
    async def edge_analysis_ui() -> HTMLResponse:
        return HTMLResponse(EDGE_HTML)
