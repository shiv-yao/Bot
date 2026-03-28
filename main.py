import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI

STATE = {
    "positions": [],
    "signals": 0,
    "errors": 0,
    "last_action": None,
}

# ================= BOT LOOP =================
async def bot_loop():
    while True:
        try:
            # 模擬掃描
            STATE["signals"] += 1
            STATE["last_action"] = "scan"

        except Exception as e:
            STATE["errors"] += 1

        await asyncio.sleep(2)

# ================= APP =================
bot_task = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot_task
    bot_task = asyncio.create_task(bot_loop())
    print("BOT STARTED")

    yield

    if bot_task:
        bot_task.cancel()

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def root():
    return {"ok": True}

@app.get("/metrics")
async def metrics():
    return STATE
