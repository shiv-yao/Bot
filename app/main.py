import asyncio
from fastapi import FastAPI
from contextlib import asynccontextmanager

from app.engine import run_engine
from app.state import engine

BOT_TASK = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global BOT_TASK
    engine.running = True
    BOT_TASK = asyncio.create_task(run_engine())
    yield
    engine.running = False
    if BOT_TASK:
        BOT_TASK.cancel()


app = FastAPI(lifespan=lifespan)


@app.get("/")
def root():
    return {
        "status": "running",
        "mode": engine.mode,
        "positions": len(engine.positions),
        "last_signal": engine.last_signal,
    }


@app.get("/debug")
def debug():
    return {
        "positions": engine.positions,
        "stats": engine.stats,
        "last_signal": engine.last_signal,
        "logs": engine.logs[-20:],
    }
