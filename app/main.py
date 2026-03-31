import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

BOT_TASK = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global BOT_TASK
    print("APP STARTING...")

    try:
        from app.engine import main_loop
        BOT_TASK = asyncio.create_task(main_loop())
        print("ENGINE TASK CREATED")
    except Exception as e:
        print("ENGINE IMPORT ERROR:", repr(e))

    yield

    if BOT_TASK:
        BOT_TASK.cancel()
        try:
            await BOT_TASK
        except Exception:
            pass

    print("APP SHUTDOWN")


app = FastAPI(lifespan=lifespan)


@app.get("/")
def root():
    return {"status": "BOOT OK"}


@app.get("/debug")
def debug():
    from app.state import engine
    return {
        "running": engine.running,
        "positions": engine.positions,
        "stats": engine.stats,
        "last_signal": engine.last_signal,
        "logs": engine.logs[-20:],
        "capital": engine.capital,
    }
