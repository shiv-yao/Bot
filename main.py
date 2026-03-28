import asyncio
import random
import httpx

from contextlib import asynccontextmanager
from fastapi import FastAPI

STATE = {
    "positions": [],
    "signals": 0,
    "errors": 0,
    "last_action": None,
    "candidates": [],
    "scanner_mode": None,
    "pump_status": None,
    "pump_error": None,
}

MAX_POSITIONS = 3


def has_position(mint: str) -> bool:
    return any(p.get("token") == mint for p in STATE["positions"])


async def real_alpha(mint: str) -> float:
    try:
        sol = "So11111111111111111111111111111111111111112"

        async with httpx.AsyncClient(timeout=8) as client:
            r1 = await client.get(
                "https://lite-api.jup.ag/swap/v1/quote",
                params={
                    "inputMint": sol,
                    "outputMint": mint,
                    "amount": "1000000",
                    "slippageBps": 100,
                },
            )

            if r1.status_code != 200:
                return 0.0

            q1 = r1.json()
            out1 = int(q1.get("outAmount", 0) or 0)
            impact = float(q1.get("priceImpactPct", 1) or 1)

            await asyncio.sleep(0.2)

            r2 = await client.get(
                "https://lite-api.jup.ag/swap/v1/quote",
                params={
                    "inputMint": sol,
                    "outputMint": mint,
                    "amount": "1000000",
                    "slippageBps": 100,
                },
            )

            if r2.status_code != 200:
                return 0.0

            q2 = r2.json()
            out2 = int(q2.get("outAmount", 0) or 0)

            if out1 <= 0:
                return 0.0

            strength = (out2 - out1) / out1
            liquidity_score = min(out1 / 100000, 3) * 25
            impact_penalty = impact * 100

            alpha = strength * 4000 + liquidity_score - impact_penalty
            return round(alpha, 2)

    except Exception:
        return 0.0


async def scan_tokens():
    tokens = []

    # source 1: pump.fun
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get("https://frontend-api.pump.fun/coins")
            STATE["pump_status"] = r.status_code

            if r.status_code == 200:
                data = r.json()

                if isinstance(data, list):
                    rows = data
                elif isinstance(data, dict):
                    rows = data.get("coins") or data.get("data") or []
                else:
                    rows = []

                for item in rows[:10]:
                    mint = item.get("mint")
                    if mint:
                        tokens.append(mint)

    except Exception as e:
        STATE["pump_error"] = str(e)

    # fallback: real-looking mints to keep pipeline testable
    if not tokens:
        tokens = [
            "So11111111111111111111111111111111111111112",
            "Es9vMFrzaCERmJfrF4H2Fy7pRkNvztNFVQVw1Gc7emsK",
        ]
        STATE["scanner_mode"] = "fallback_real_mints"
    else:
        STATE["scanner_mode"] = "pumpfun"

    return tokens


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

                if mint.startswith("TEST_"):
                    continue

                alpha = await real_alpha(mint)

                if alpha < 120:
                    continue

                STATE["positions"].append({
                    "token": mint,
                    "alpha": round(alpha, 2),
                    "size": 0.01,
                    "entry_price": round(random.uniform(0.00001, 0.00002), 8),
                })
                STATE["last_action"] = f"paper_buy:{mint}"

        except Exception as e:
            STATE["errors"] += 1
            STATE["last_action"] = f"error:{str(e)}"

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
        "scanner_mode": STATE.get("scanner_mode"),
        "pump_status": STATE.get("pump_status"),
        "pump_error": STATE.get("pump_error"),
    }
