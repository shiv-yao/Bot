import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from bot import bot_loop
from state import engine

bot_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot_task

    engine.log("🚀 APP STARTING")
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
    return {
        "ok": True,
        "message": "Pump Trading Engine Running",
        "mode": engine.mode,
    }


@app.get("/health")
async def health():
    data = engine.snapshot()
    return {
        "ok": data.get("bot_ok", True),
        "mode": data.get("mode"),
        "errors": data.get("stats", {}).get("errors", 0),
        "positions": len(data.get("positions", [])),
        "last_trade": data.get("last_trade", ""),
    }


@app.get("/data")
async def data():
    return JSONResponse(engine.snapshot())
