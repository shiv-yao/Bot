import os
import json
import asyncio
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from state import engine
from bot import bot_loop

BOT_TASK = None


# ================= INIT =================
def init_engine():
    if not hasattr(engine, "running"):
        engine.running = True

    if not hasattr(engine, "positions") or not isinstance(engine.positions, list):
        engine.positions = []

    if not hasattr(engine, "logs") or not isinstance(engine.logs, list):
        engine.logs = []

    if not hasattr(engine, "trade_history") or not isinstance(engine.trade_history, list):
        engine.trade_history = []

    if not hasattr(engine, "stats") or not isinstance(engine.stats, dict):
        engine.stats = {
            "signals": 0,
            "buys": 0,
            "sells": 0,
            "errors": 0,
            "adds": 0,
        }

    if not hasattr(engine, "engine_stats") or not isinstance(engine.engine_stats, dict):
        engine.engine_stats = {
            "stable": {"pnl": 0.0, "trades": 0, "wins": 0},
            "degen": {"pnl": 0.0, "trades": 0, "wins": 0},
            "sniper": {"pnl": 0.0, "trades": 0, "wins": 0},
        }

    if not hasattr(engine, "engine_allocator") or not isinstance(engine.engine_allocator, dict):
        engine.engine_allocator = {
            "stable": 0.4,
            "degen": 0.4,
            "sniper": 0.2,
        }

    if not hasattr(engine, "last_trade"):
        engine.last_trade = ""

    if not hasattr(engine, "last_signal"):
        engine.last_signal = ""

    if not hasattr(engine, "candidate_count"):
        engine.candidate_count = 0

    if not hasattr(engine, "capital"):
        engine.capital = 30.0

    if not hasattr(engine, "sol_balance"):
        engine.sol_balance = 30.0

    if not hasattr(engine, "bot_ok"):
        engine.bot_ok = True

    if not hasattr(engine, "bot_error"):
        engine.bot_error = ""

    if not hasattr(engine, "mode"):
        engine.mode = "PAPER"


# ================= ENV / STATUS =================
def get_mode():
    real_trading = os.environ.get("REAL_TRADING", "false").lower() == "true"
    jup_api_key = bool(os.environ.get("JUP_API_KEY", "").strip())
    pk_json = bool(os.environ.get("PRIVATE_KEY_JSON", "").strip())
    pk_b58 = bool(os.environ.get("PRIVATE_KEY_B58", "").strip())

    ready = real_trading and jup_api_key and (pk_json or pk_b58)
    return "REAL" if ready else "PAPER"


def get_rpc_http_list():
    raw = os.environ.get(
        "SOLANA_RPC_HTTPS",
        os.environ.get("SOLANA_RPC_HTTP", "https://api.mainnet-beta.solana.com"),
    )
    return [x.strip() for x in raw.split(",") if x.strip()]


def get_rpc_ws_list():
    raw = os.environ.get(
        "SOLANA_RPC_WSS",
        os.environ.get("SOLANA_RPC_WS", "wss://api.mainnet-beta.solana.com"),
    )
    return [x.strip() for x in raw.split(",") if x.strip()]


async def check_http_rpc(url: str):
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getHealth",
        "params": [],
    }
    try:
        async with httpx.AsyncClient(timeout=6.0, follow_redirects=True) as client:
            r = await client.post(url, json=payload)
        if r.status_code != 200:
            return {"url": url, "ok": False, "detail": f"http_{r.status_code}"}

        data = r.json()
        if "result" in data:
            return {"url": url, "ok": True, "detail": str(data["result"])}
        if "error" in data:
            return {"url": url, "ok": False, "detail": str(data["error"])}
        return {"url": url, "ok": False, "detail": "unknown_response"}
    except Exception as e:
        return {"url": url, "ok": False, "detail": str(e)[:180]}


async def check_ws_rpc(url: str):
    try:
        import websockets

        async with websockets.connect(
            url,
            ping_interval=10,
            ping_timeout=10,
            close_timeout=3,
            max_size=2**20,
        ) as ws:
            req = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getHealth",
                "params": [],
            }
            await ws.send(json.dumps(req))
            raw = await asyncio.wait_for(ws.recv(), timeout=6)
            data = json.loads(raw)

            if "result" in data:
                return {"url": url, "ok": True, "detail": str(data["result"])}
            if "error" in data:
                return {"url": url, "ok": False, "detail": str(data["error"])}
            return {"url": url, "ok": False, "detail": "unknown_response"}
    except Exception as e:
        return {"url": url, "ok": False, "detail": str(e)[:180]}


async def collect_runtime_status():
    init_engine()

    mode = get_mode()
    engine.mode = mode

    rpc_https = get_rpc_http_list()
    rpc_wss = get_rpc_ws_list()

    http_checks = await asyncio.gather(*[check_http_rpc(u) for u in rpc_https])
    ws_checks = await asyncio.gather(*[check_ws_rpc(u) for u in rpc_wss])

    jup_api = bool(os.environ.get("JUP_API_KEY", "").strip())
    use_jito = os.environ.get("USE_JITO", "false").lower() == "true"
    jito_url = bool(os.environ.get("JITO_BUNDLE_URL", "").strip())

    return {
        "mode": mode,
        "bot_ok": bool(getattr(engine, "bot_ok", True)),
        "bot_error": str(getattr(engine, "bot_error", "")),
        "jup_api_key_present": jup_api,
        "use_jito": use_jito,
        "jito_url_present": jito_url,
        "rpc_http": http_checks,
        "rpc_ws": ws_checks,
        "stats": getattr(engine, "stats", {}),
        "engine_stats": getattr(engine, "engine_stats", {}),
        "engine_allocator": getattr(engine, "engine_allocator", {}),
        "candidate_count": getattr(engine, "candidate_count", 0),
        "capital": getattr(engine, "capital", 0.0),
        "sol_balance": getattr(engine, "sol_balance", 0.0),
        "last_trade": getattr(engine, "last_trade", ""),
        "last_signal": getattr(engine, "last_signal", ""),
        "positions": getattr(engine, "positions", []),
        "trade_history": getattr(engine, "trade_history", []),
        "logs": getattr(engine, "logs", [])[-100:],
    }


# ================= APP LIFECYCLE =================
@asynccontextmanager
async def lifespan(app: FastAPI):
    global BOT_TASK
    init_engine()

    if BOT_TASK is None or BOT_TASK.done():
        BOT_TASK = asyncio.create_task(bot_loop())

    yield

    if BOT_TASK and not BOT_TASK.done():
        BOT_TASK.cancel()
        try:
            await BOT_TASK
        except Exception:
            pass


app = FastAPI(title="Trading Bot Dashboard", lifespan=lifespan)


# ================= ROUTES =================
@app.get("/health")
async def health():
    init_engine()
    return {
        "ok": True,
        "mode": get_mode(),
        "bot_ok": getattr(engine, "bot_ok", True),
        "bot_error": getattr(engine, "bot_error", ""),
    }


@app.get("/debug")
async def debug():
    data = await collect_runtime_status()
    return JSONResponse(content=data)


@app.get("/api/status")
async def api_status():
    data = await collect_runtime_status()
    return JSONResponse(content=data)


@app.get("/", response_class=HTMLResponse)
async def home():
    data = await collect_runtime_status()

    def badge(ok: bool, text_ok="OK", text_bad="BAD"):
        bg = "#16a34a" if ok else "#dc2626"
        text = text_ok if ok else text_bad
        return f'<span style="display:inline-block;padding:4px 10px;border-radius:999px;background:{bg};color:white;font-weight:700;">{text}</span>'

    def mode_badge(mode: str):
        bg = "#dc2626" if mode == "REAL" else "#2563eb"
        return f'<span style="display:inline-block;padding:6px 12px;border-radius:999px;background:{bg};color:white;font-weight:800;">{mode}</span>'

    def render_rpc_rows(rows):
        html = ""
        for row in rows:
            ok = row.get("ok", False)
            detail = row.get("detail", "")
            url = row.get("url", "")
            html += f"""
            <tr>
                <td style="padding:8px;border-bottom:1px solid #222;">{url}</td>
                <td style="padding:8px;border-bottom:1px solid #222;">{badge(ok)}</td>
                <td style="padding:8px;border-bottom:1px solid #222;color:#aaa;">{detail}</td>
            </tr>
            """
        return html or """
        <tr><td colspan="3" style="padding:8px;color:#aaa;">No RPC configured</td></tr>
        """

    positions_html = ""
    for p in data["positions"][-20:]:
        positions_html += f"""
        <tr>
            <td style="padding:8px;border-bottom:1px solid #222;">{p.get("token","")[:10]}</td>
            <td style="padding:8px;border-bottom:1px solid #222;">{p.get("engine","")}</td>
            <td style="padding:8px;border-bottom:1px solid #222;">{round(float(p.get("entry_price", 0) or 0), 10)}</td>
            <td style="padding:8px;border-bottom:1px solid #222;">{round(float(p.get("last_price", 0) or 0), 10)}</td>
            <td style="padding:8px;border-bottom:1px solid #222;">{round(float(p.get("pnl_pct", 0) or 0) * 100, 2)}%</td>
            <td style="padding:8px;border-bottom:1px solid #222;">{p.get("trade_mode","")}</td>
        </tr>
        """
    if not positions_html:
        positions_html = '<tr><td colspan="6" style="padding:8px;color:#aaa;">No open positions</td></tr>'

    trades_html = ""
    for t in data["trade_history"][-20:][::-1]:
        trades_html += f"""
        <tr>
            <td style="padding:8px;border-bottom:1px solid #222;">{t.get("token","")[:10]}</td>
            <td style="padding:8px;border-bottom:1px solid #222;">{t.get("engine","")}</td>
            <td style="padding:8px;border-bottom:1px solid #222;">{round(float(t.get("pnl_pct", 0) or 0) * 100, 2)}%</td>
            <td style="padding:8px;border-bottom:1px solid #222;">{t.get("trade_mode","")}</td>
            <td style="padding:8px;border-bottom:1px solid #222;">{t.get("entry_signature","") or ""}</td>
            <td style="padding:8px;border-bottom:1px solid #222;">{t.get("exit_signature","") or ""}</td>
        </tr>
        """
    if not trades_html:
        trades_html = '<tr><td colspan="6" style="padding:8px;color:#aaa;">No trade history</td></tr>'

    logs_html = "<br>".join(
        str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        for x in data["logs"][-80:]
    )

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8"/>
        <meta name="viewport" content="width=device-width, initial-scale=1"/>
        <meta http-equiv="refresh" content="8"/>
        <title>Trading Bot Dashboard</title>
        <style>
            body {{
                margin: 0;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
                background: #0b0f19;
                color: #f3f4f6;
            }}
            .wrap {{
                max-width: 1400px;
                margin: 0 auto;
                padding: 24px;
            }}
            .grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
                gap: 16px;
            }}
            .card {{
                background: #111827;
                border: 1px solid #1f2937;
                border-radius: 16px;
                padding: 18px;
                box-shadow: 0 4px 18px rgba(0,0,0,0.25);
            }}
            .title {{
                font-size: 13px;
                color: #9ca3af;
                margin-bottom: 8px;
                text-transform: uppercase;
                letter-spacing: 0.06em;
            }}
            .value {{
                font-size: 28px;
                font-weight: 800;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                font-size: 14px;
            }}
            .section-title {{
                font-size: 20px;
                font-weight: 800;
                margin: 24px 0 12px;
            }}
            .mono {{
                font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
                word-break: break-all;
            }}
            .small {{
                font-size: 12px;
                color: #9ca3af;
            }}
            .logs {{
                background: #050814;
                border: 1px solid #1f2937;
                border-radius: 16px;
                padding: 16px;
                min-height: 240px;
                max-height: 420px;
                overflow: auto;
                font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
                font-size: 12px;
                line-height: 1.5;
                white-space: pre-wrap;
            }}
        </style>
    </head>
    <body>
        <div class="wrap">
            <div style="display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap;">
                <div>
                    <div style="font-size:32px;font-weight:900;">Trading Bot Dashboard</div>
                    <div class="small">Auto refresh every 8s</div>
                </div>
                <div>{mode_badge(data["mode"])}</div>
            </div>

            <div class="grid" style="margin-top:18px;">
                <div class="card">
                    <div class="title">Bot Status</div>
                    <div class="value">{badge(data["bot_ok"], "RUNNING", "ERROR")}</div>
                    <div class="small" style="margin-top:10px;">{data["bot_error"] or "No error"}</div>
                </div>
                <div class="card">
                    <div class="title">Capital</div>
                    <div class="value">{round(float(data["capital"]), 4)}</div>
                </div>
                <div class="card">
                    <div class="title">SOL Balance</div>
                    <div class="value">{round(float(data["sol_balance"]), 4)}</div>
                </div>
                <div class="card">
                    <div class="title">Candidates</div>
                    <div class="value">{int(data["candidate_count"])}</div>
                </div>
            </div>

            <div class="grid" style="margin-top:16px;">
                <div class="card"><div class="title">Signals</div><div class="value">{int(data["stats"].get("signals", 0))}</div></div>
                <div class="card"><div class="title">Buys</div><div class="value">{int(data["stats"].get("buys", 0))}</div></div>
                <div class="card"><div class="title">Sells</div><div class="value">{int(data["stats"].get("sells", 0))}</div></div>
                <div class="card"><div class="title">Errors</div><div class="value">{int(data["stats"].get("errors", 0))}</div></div>
                <div class="card"><div class="title">Adds</div><div class="value">{int(data["stats"].get("adds", 0))}</div></div>
            </div>

            <div class="grid" style="margin-top:16px;">
                <div class="card">
                    <div class="title">Jupiter API Key</div>
                    <div class="value">{badge(data["jup_api_key_present"], "PRESENT", "MISSING")}</div>
                </div>
                <div class="card">
                    <div class="title">Jito Enabled</div>
                    <div class="value">{badge(data["use_jito"], "ON", "OFF")}</div>
                    <div class="small" style="margin-top:10px;">Bundle URL: {"present" if data["jito_url_present"] else "missing"}</div>
                </div>
                <div class="card">
                    <div class="title">Last Trade</div>
                    <div class="small mono">{data["last_trade"] or "-"}</div>
                </div>
                <div class="card">
                    <div class="title">Last Signal</div>
                    <div class="small mono">{data["last_signal"] or "-"}</div>
                </div>
            </div>

            <div class="section-title">RPC HTTP Status</div>
            <div class="card">
                <table>
                    <thead>
                        <tr>
                            <th style="text-align:left;padding:8px;border-bottom:1px solid #222;">URL</th>
                            <th style="text-align:left;padding:8px;border-bottom:1px solid #222;">Status</th>
                            <th style="text-align:left;padding:8px;border-bottom:1px solid #222;">Detail</th>
                        </tr>
                    </thead>
                    <tbody>
                        {render_rpc_rows(data["rpc_http"])}
                    </tbody>
                </table>
            </div>

            <div class="section-title">RPC WS Status</div>
            <div class="card">
                <table>
                    <thead>
                        <tr>
                            <th style="text-align:left;padding:8px;border-bottom:1px solid #222;">URL</th>
                            <th style="text-align:left;padding:8px;border-bottom:1px solid #222;">Status</th>
                            <th style="text-align:left;padding:8px;border-bottom:1px solid #222;">Detail</th>
                        </tr>
                    </thead>
                    <tbody>
                        {render_rpc_rows(data["rpc_ws"])}
                    </tbody>
                </table>
            </div>

            <div class="section-title">Engine Allocator</div>
            <div class="card">
                <div class="mono">{json.dumps(data["engine_allocator"], ensure_ascii=False, indent=2)}</div>
            </div>

            <div class="section-title">Engine Stats</div>
            <div class="card">
                <div class="mono">{json.dumps(data["engine_stats"], ensure_ascii=False, indent=2)}</div>
            </div>

            <div class="section-title">Open Positions</div>
            <div class="card">
                <table>
                    <thead>
                        <tr>
                            <th style="text-align:left;padding:8px;border-bottom:1px solid #222;">Token</th>
                            <th style="text-align:left;padding:8px;border-bottom:1px solid #222;">Engine</th>
                            <th style="text-align:left;padding:8px;border-bottom:1px solid #222;">Entry</th>
                            <th style="text-align:left;padding:8px;border-bottom:1px solid #222;">Last</th>
                            <th style="text-align:left;padding:8px;border-bottom:1px solid #222;">PnL</th>
                            <th style="text-align:left;padding:8px;border-bottom:1px solid #222;">Mode</th>
                        </tr>
                    </thead>
                    <tbody>
                        {positions_html}
                    </tbody>
                </table>
            </div>

            <div class="section-title">Trade History</div>
            <div class="card">
                <table>
                    <thead>
                        <tr>
                            <th style="text-align:left;padding:8px;border-bottom:1px solid #222;">Token</th>
                            <th style="text-align:left;padding:8px;border-bottom:1px solid #222;">Engine</th>
                            <th style="text-align:left;padding:8px;border-bottom:1px solid #222;">PnL</th>
                            <th style="text-align:left;padding:8px;border-bottom:1px solid #222;">Mode</th>
                            <th style="text-align:left;padding:8px;border-bottom:1px solid #222;">Entry Sig</th>
                            <th style="text-align:left;padding:8px;border-bottom:1px solid #222;">Exit Sig</th>
                        </tr>
                    </thead>
                    <tbody>
                        {trades_html}
                    </tbody>
                </table>
            </div>

            <div class="section-title">Recent Logs</div>
            <div class="logs">{logs_html or "No logs yet"}</div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(html)
