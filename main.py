import asyncio
import random
from contextlib import asynccontextmanager
from fastapi import FastAPI

STATE = {
    "positions": [],
    "signals": 0,
    "errors": 0,
    "last_action": None,
    "candidates": [],
}

MAX_POSITIONS = 3


async def scan_tokens():
    # 先用假資料測流程，之後再換真 scanner
    sample = [
        "TOKEN_A",
        "TOKEN_B",
        "TOKEN_C",
        "TOKEN_D",
    ]
    random.shuffle(sample)
    return sample[:2]


def has_position(mint: str) -> bool:
    return any(p["token"] == mint for p in STATE["positions"])


def fake_alpha(mint: str) -> float:
    return random.uniform(80, 180)


async def bot_loop():
    while True:
        try:
            STATE["signals"] += 1
            STATE["last_action"] = "scan"

            tokens = await scan_tokens()
            STATE["candidates"] = tokens

            for mint in tokens:
                if len(STATE["positions"]) >= MAX_POSITIONS:
                    break

                if has_position(mint):
                    continue

                alpha = fake_alpha(mint)

                if alpha < 120:
                    continue

                # 模擬買入
                STATE["positions"].append({
                    "token": mint,
                    "alpha": round(alpha, 2),
                    "size": 0.01,
                    "entry_price": round(random.uniform(0.00001, 0.00002), 8),
                })
                STATE["last_action"] = f"paper_buy:{mint}"

        except Exception:
            STATE["errors"] += 1

        await asyncio.sleep(2)


bot_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot_task
    bot_task = asyncio.create_task(bot_loop())
    yield
    if bot_task:
        bot_task.cancel()


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def root():
    return {"ok": True, "status": "running"}


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/data")
async def data():
    return STATE


@app.get("/metrics")
async def metrics():
    return {
        "positions": STATE["positions"],
        "signals": STATE["signals"],
        "errors": STATE["errors"],
        "last_action": STATE["last_action"],
        "candidates": STATE["candidates"],
    }
