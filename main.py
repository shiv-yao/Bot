import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from bot import bot_loop, repair, real_trading_ready
from state import engine

bot_task = None


def ensure_runtime_state():
    repair()
    engine.mode = "REAL" if real_trading_ready() else "PAPER"
    if not hasattr(engine, "running"):
        engine.running = True


@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot_task
    ensure_runtime_state()
    engine.log(f"🚀 APP STARTING mode={engine.mode}")
    bot_task = asyncio.create_task(bot_loop())

    try:
        yield
    finally:
        engine.log("🛑 APP SHUTTING DOWN")
        if bot_task:
            bot_task.cancel()
            try:
                await bot_task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="Pump Trading Engine", lifespan=lifespan)


@app.get("/")
async def root():
    ensure_runtime_state()
    snap = engine.snapshot()
    return {
        "ok": True,
        "message": "Pump Trading Engine Running",
        "mode": snap.get("mode", engine.mode),
        "positions": len(snap.get("positions", [])),
        "candidate_count": snap.get("candidate_count", 0),
        "last_trade": snap.get("last_trade", ""),
    }


@app.get("/health")
async def health():
    ensure_runtime_state()
    data = engine.snapshot()
    return {
        "ok": data.get("bot_ok", True),
        "mode": data.get("mode", engine.mode),
        "wallet_ok": data.get("wallet_ok", False),
        "jup_ok": data.get("jup_ok", False),
        "errors": data.get("stats", {}).get("errors", 0),
        "positions": len(data.get("positions", [])),
        "last_signal": data.get("last_signal", ""),
        "last_trade": data.get("last_trade", ""),
        "bot_error": data.get("bot_error", ""),
    }


@app.get("/data")
async def data():
    ensure_runtime_state()
    return JSONResponse(engine.snapshot())


@app.get("/debug")
async def debug():
    ensure_runtime_state()
    return JSONResponse(engine.snapshot())


@app.get("/ping")
async def ping():
    return {"ok": True}
