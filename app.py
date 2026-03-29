import os
import asyncio
from init_engine import init_engine
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from state import engine

BOT_TASK = None





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
