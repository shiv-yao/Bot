import os
import asyncio
from init_engine import init_engine
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from state import engine

BOT_TASK = None


def init_engine():
    engine.running = True
    engine.mode = "REAL" if os.environ.get("REAL_TRADING", "false").lower() == "true" else "PAPER"

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
            "adds": 0
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

    if not hasattr(engine, "bot_ok"):
        engine.bot_ok = True

    if not hasattr(engine, "bot_error"):
        engine.bot_error = ""

    if not hasattr(engine, "candidate_count"):
        engine.candidate_count = 0

    if not hasattr(engine, "capital"):
        engine.capital = 30.0

    if not hasattr(engine, "last_signal"):
        engine.last_signal = ""

    if not hasattr(engine, "last_trade"):
        engine.last_trade = ""


async def start_bot():
    global BOT_TASK

    if BOT_TASK and not BOT_TASK.done():
        return

    try:
        from bot import bot_loop
        BOT_TASK = asyncio.create_task(bot_loop())
        engine.bot_ok = True
        engine.bot_error = ""
    except Exception as e:
        engine.bot_ok = False
        engine.bot_error = str(e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_engine()
    await start_bot()
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/")
def root():
    return {
        "name": "SOLANA AI BOT",
        "mode": engine.mode,
        "status": "running"
    }


@app.get("/health")
def health():
    return {
        "ok": True,
        "mode": engine.mode,
        "bot_ok": engine.bot_ok,
        "bot_error": engine.bot_error,
        "positions": len(engine.positions),
        "trades": len(engine.trade_history),
    }


@app.get("/data")
def data():
    try:
        return {
            "mode": engine.mode,
            "positions": engine.positions,
            "trade_history": engine.trade_history[-50:],
            "stats": engine.stats,
            "engine_stats": engine.engine_stats,
            "allocator": engine.engine_allocator,
            "candidate_count": engine.candidate_count,
            "capital": engine.capital,
            "last_signal": engine.last_signal,
            "last_trade": engine.last_trade,
            "logs": engine.logs[-100:]
        }
    except Exception as e:
        return JSONResponse({"error": str(e)})


@app.get("/debug")
def debug():
    return {
        "mode": engine.mode,
        "REAL_TRADING": os.environ.get("REAL_TRADING"),
        "RPC": os.environ.get("SOLANA_RPC_HTTP"),
        "has_private_key": bool(os.environ.get("PRIVATE_KEY_JSON")),
        "has_jup_key": bool(os.environ.get("JUP_API_KEY")),
        "bot_ok": engine.bot_ok,
        "bot_error": engine.bot_error,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000)),
        reload=False
    )
