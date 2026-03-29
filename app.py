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
    engine.mode = getattr(engine, "mode", "PAPER")


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
        engine.set_error(str(e))


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
        "bot_ok": engine.bot_ok,
        "bot_error": engine.bot_error,
    }


@app.get("/data")
def data():
    try:
        return engine.snapshot()
    except Exception as e:
        return JSONResponse({
            "error": str(e)
        })


# ================= RUN =================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
