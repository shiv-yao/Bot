from fastapi import FastAPI
from state import engine

from contextlib import asynccontextmanager
import asyncio
from fastapi import FastAPI

from bot import bot_loop   # 🔥 關鍵

@asynccontextmanager
async def lifespan(app: FastAPI):

    print("🚀 BOT STARTING...")

    asyncio.create_task(bot_loop())   # 🔥 核心

    yield

app = FastAPI(lifespan=lifespan)

@app.get("/data")
def data():
    return {
        "capital": engine.capital,
        "positions": engine.positions,
        "trades": engine.trade_history[-200:],
        "logs": engine.logs[-50:],
        "stats": engine.stats,
    }

@app.post("/buy")
def buy(data: dict):
    engine.log(f"📱 BUY {data}")
    return {"ok": True}

@app.post("/sell")
def sell(data: dict):
    engine.log(f"📱 SELL {data}")
    return {"ok": True}

@app.post("/kill")
def kill():
    engine.kill = True
    return {"status": "stopped"}
