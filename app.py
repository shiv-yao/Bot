01"}
import os
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from state import engine

BOT_TASK = None


# ================= INIT =================
def init_engine():
    engine.running = True
    engine.mode = "REAL" if os.environ.get("REAL_TRADING") == "true" else "PAPER"

    if not hasattr(engine, "positions"):
        engine.positions = []

    if not hasattr(engine, "logs"):
        engine.logs = []

    if not hasattr(engine, "trade_history"):
        engine.trade_history = []

    if not hasattr(engine, "stats"):
        engine.stats = {
            "signals": 0,
            "buys": 0,
            "sells": 0,
            "errors": 0,
            "adds": 0
        }

    if not hasattr(engine, "engine_stats"):
        engine.engine_stats = {
            "stable": {"pnl": 0, "trades": 0, "wins": 0},
            "degen": {"pnl": 0, "trades": 0, "wins": 0},
            "sniper": {"pnl": 0, "trades": 0, "wins": 0},
        }

    if not hasattr(engine, "engine_allocator"):
        engine.engine_allocator = {
            "stable": 0.4,
            "degen": 0.4,
            "sniper": 0.2,
        }

    if not hasattr(engine, "bot_ok"):
        engine.bot_ok = True

    if not hasattr(engine, "bot_error"):
        engine.bot_error = ""


# ================= BOT =================
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


# ================= LIFESPAN =================
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_engine()
    await start_bot()
    yield


app = FastAPI(lifespan=lifespan)


# ================= API =================
@app.get("/health")
def health():
    return {
        "ok": True,
        "mode": engine.mode,
        "bot_ok": engine.bot_ok,
        "bot_error": engine.bot_error,
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
            "logs": engine.logs[-50:]
        }
    except Exception as e:
        return JSONResponse({"error": str(e)})


# ================= RUN =================
if name == "main":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 800
