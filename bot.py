# ================= v960_FINAL_FULL =================

import os, asyncio, random, time
from collections import defaultdict
import httpx

from state import engine
from mempool import mempool_stream

# ================= CONFIG =================

RPC = os.getenv("RPC", "")
SOL = "So11111111111111111111111111111111111111112"

MAX_POSITION_SOL = 0.0025
MIN_POSITION_SOL = 0.001

SEED_TOKENS = [
    SOL,
    "EPjFWdd5AufqSSqeM2q7KZ1xzy6h7Q5Gk1s7k9KkZx9"  # USDC
]

# ================= INIT =================

if not hasattr(engine, "positions"):
    engine.positions = []

engine.logs = []
engine.capital = 1.0
engine.sol_balance = 1.0

# ================= ENGINE SYSTEM =================

ENGINE_STATS = {
    "stable": {"pnl": 0.0, "trades": 0, "wins": 0},
    "degen": {"pnl": 0.0, "trades": 0, "wins": 0},
    "sniper": {"pnl": 0.0, "trades": 0, "wins": 0},
}

ENGINE_ALLOCATOR = {
    "stable": 0.4,
    "degen": 0.4,
    "sniper": 0.2,
}

ENGINE_BASE_SIZE = {
    "stable": 0.003,
    "degen": 0.002,
    "sniper": 0.0015,
}

ALPHA_MEMORY = {
    "stable": [],
    "degen": [],
    "sniper": [],
}

MAX_POSITION_PER_ENGINE = 3

# ================= STATE =================

CANDIDATES = set(SEED_TOKENS)

# ================= UTILS =================

def clamp(v, lo, hi):
    return max(lo, min(v, hi))

# ================= PRICE =================

async def get_price(mint):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://lite-api.jup.ag/swap/v1/quote",
                params={
                    "inputMint": mint,
                    "outputMint": SOL,
                    "amount": "1000000"
                }
            )
        j = r.json()
        out = int(j.get("outAmount", 0)) / 1e9
        return out / 1_000_000 if out > 0 else None
    except:
        return None

# ================= ALPHA =================

async def momentum(mint):
    p1 = await get_price(mint)
    await asyncio.sleep(0.2)
    p2 = await get_price(mint)

    if not p1 or not p2 or p1 <= 0:
        return 0

    return ((p2 - p1) / p1) * 5  # 🔥 強化 alpha

async def liquidity_ok(mint):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://lite-api.jup.ag/swap/v1/quote",
                params={
                    "inputMint": SOL,
                    "outputMint": mint,
                    "amount": "10000000"
                }
            )
        j = r.json()
        impact = float(j.get("priceImpactPct", 1))
        return impact < 0.25
    except:
        return False

async def anti_rug(mint):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://lite-api.jup.ag/swap/v1/quote",
                params={
                    "inputMint": mint,
                    "outputMint": SOL,
                    "amount": "1000000"
                }
            )
        j = r.json()
        return int(j.get("outAmount", 0)) > 0
    except:
        return False

# ================= ENGINE LOGIC =================

def update_alpha_memory(engine_name, alpha, pnl):
    mem = ALPHA_MEMORY[engine_name]
    mem.append((alpha, pnl))
    if len(mem) > 100:
        mem.pop(0)

def get_alpha_edge(engine_name, alpha):
    mem = ALPHA_MEMORY[engine_name]
    if not mem:
        return 1.0

    sim = [p for a,p in mem if abs(a-alpha) < 0.01]
    if not sim:
        return 1.0

    avg = sum(sim)/len(sim)
    return clamp(1 + avg*5, 0.5, 2.0)

def pick_engine(alpha):
    if alpha > 0.02:
        return "sniper"
    return random.choices(
        ["stable","degen","sniper"],
        weights=[
            ENGINE_ALLOCATOR["stable"],
            ENGINE_ALLOCATOR["degen"],
            ENGINE_ALLOCATOR["sniper"]
        ]
    )[0]

def engine_size(name, alpha):
    base = ENGINE_BASE_SIZE[name]
    edge = get_alpha_edge(name, alpha)
    size = base * (1 + alpha*10) * edge
    return clamp(size, MIN_POSITION_SOL, MAX_POSITION_SOL)

# ================= EXEC =================

async def buy(mint, alpha):

    if any(p["token"] == mint for p in engine.positions):
        return False

    eng = pick_engine(alpha)

    if sum(1 for p in engine.positions if p.get("engine") == eng) >= MAX_POSITION_PER_ENGINE:
        return False

    price = await get_price(mint)
    if not price:
        return False

    size = engine_size(eng, alpha)
    amount = size / price

    engine.positions.append({
        "token": mint,
        "amount": amount,
        "entry": price,
        "engine": eng,
        "alpha": alpha,
        "peak": price
    })

    engine.logs.append(f"BUY {mint[:6]} {eng} α={round(alpha,5)}")
    engine.logs = engine.logs[-100:]

    print(f"🟢 BUY {mint[:6]} {eng}")
    return True

async def sell(pos):

    mint = pos["token"]
    price = await get_price(mint)
    if not price:
        return

    pnl = (price - pos["entry"]) * pos["amount"]
    eng = pos["engine"]

    ENGINE_STATS[eng]["trades"] += 1
    ENGINE_STATS[eng]["pnl"] += pnl

    if pnl > 0:
        ENGINE_STATS[eng]["wins"] += 1

    update_alpha_memory(eng, pos["alpha"], pnl)

    engine.positions.remove(pos)

    engine.logs.append(f"SELL {mint[:6]} pnl={round(pnl,6)}")
    engine.logs = engine.logs[-100:]

    print(f"🔴 SELL {mint[:6]}")

# ================= MONITOR =================

async def monitor():
    while True:
        for p in list(engine.positions):

            price = await get_price(p["token"])
            if not price:
                continue

            p["peak"] = max(p["peak"], price)

            pnl_pct = (price - p["entry"]) / p["entry"]

            if pnl_pct > 0.15 or pnl_pct < -0.06:
                await sell(p)

        await asyncio.sleep(2)

# ================= MEMPOOL =================

async def handle_mempool(e):
    mint = e.get("mint")
    if mint:
        CANDIDATES.add(mint)

# ================= ALLOCATOR =================

def update_allocator():
    scores = {}
    for e in ENGINE_STATS:
        s = ENGINE_STATS[e]
        if s["trades"] == 0:
            scores[e] = 1
        else:
            win = s["wins"] / s["trades"]
            scores[e] = max(0.1, s["pnl"] * win)

    total = sum(scores.values()) + 1e-9

    for k in scores:
        ENGINE_ALLOCATOR[k] = scores[k] / total

# ================= MAIN =================

async def bot():

    print("🚀 V960 BOT START")

    asyncio.create_task(monitor())

    try:
        asyncio.create_task(mempool_stream(handle_mempool))
    except:
        pass

    while True:

        try:

            if len(CANDIDATES) < 2:
                CANDIDATES.update(SEED_TOKENS)

            for mint in list(CANDIDATES):

                alpha = await momentum(mint)

                engine.logs.append(f"SCAN {mint[:6]} α={round(alpha,5)}")
                engine.logs = engine.logs[-100:]

                # 🔥 核心濾網
                if alpha < 0.004:
                    continue

                if not await liquidity_ok(mint):
                    continue

                if not await anti_rug(mint):
                    continue

                await buy(mint, alpha)

            update_allocator()

        except Exception as e:
            print("ERR", e)

        await asyncio.sleep(2)

# ================= RAILWAY =================

async def bot_loop():
    await bot()

# ================= LOCAL =================

if __name__ == "__main__":
    asyncio.run(bot())
