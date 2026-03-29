import os
import json
import asyncio
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from state import engine

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
                "method": "Ping",
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


async def safe_bot_runner():
    try:
        from bot import bot_loop
        engine.bot_ok = True
        engine.bot_error = ""
        await bot_loop()
    except Exception as e:
        init_engine()
        engine.bot_ok = False
        engine.bot_error = f"BOT_START_ERR: {e}"
        engine.logs.append(f"BOT_START_ERR: {e}")
        engine.logs = engine.logs[-500:]


@asynccontextmanager
async def lifespan(app: FastAPI):
    global BOT_TASK
    init_engine()

    try:
        if BOT_TASK is None or BOT_TASK.done():
            BOT_TASK = asyncio.create_task(safe_bot_runner())
    except Exception as e:
        engine.bot_ok = False
        engine.bot_error = f"LIFESPAN_ERR: {e}"

    yield

    if BOT_TASK and not BOT_TASK.done():
        BOT_TASK.cancel()
        try:
            await BOT_TASK
        except BaseException:
            pass


app = FastAPI(title="Trading Bot Dashboard", lifespan=lifespan)


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
    return HTMLResponse(
        f"""
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>Bot Dashboard</title>
        </head>
        <body style="font-family: sans-serif; background:#0b0f19; color:white; padding:24px;">
            <h1>Trading Bot Dashboard</h1>
            <p>Mode: <b>{data["mode"]}</b></p>
            <p>Bot OK: <b>{data["bot_ok"]}</b></p>
            <p>Bot Error: <b>{data["bot_error"] or "-"}</b></p>
            <p><a href="/debug" style="color:#7dd3fc;">/debug</a></p>
            <p><a href="/api/status" style="color:#7dd3fc;">/api/status</a></p>
        </body>
        </html>
        """
    )
