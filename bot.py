# ================= v950_FUSED_ENGINE =================

import os, asyncio, random, time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import httpx

from state import engine
from wallet import load_keypair
from jupiter import get_order, execute_order
from mempool import mempool_stream

# ================= CONFIG =================

RPC = os.getenv("RPC", "")
SOL = "So11111111111111111111111111111111111111112"
MODE = os.getenv("MODE", "PAPER")

MAX_POSITION_SOL = 0.0025
MIN_POSITION_SOL = 0.001

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
KILL_SWITCH_LOSS_STREAK = 6

# ================= STATE =================

CANDIDATES = set()
LAST_TRADE = defaultdict(float)

# ================= UTILS =================

def now():
    return time.time()

def clamp(v, lo, hi):
    return max(lo, min(v, hi))

# ================= ALPHA =================

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
        out = int(j["outAmount"]) / 1e9
        return out / 1_000_000
    except:
        return None

async def momentum(mint):
    p1 = await get_price(mint)
    await asyncio.sleep(0.1)
    p2 = await get_price(mint)

    if not p1 or not p2:
        return 0

    return (p2 - p1) / p1

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

    sim = [p for a,p in mem if abs(a-alpha)<10]
    if not sim:
        return 1.0

    avg = sum(sim)/len(sim)
    return max(0.5, min(2.0, 1 + avg*5))

def pick_engine(alpha):
    if alpha > 0.05:
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

# ================= EXECUTION =================

async def send_tx(tx):
    async with httpx.AsyncClient() as client:
        await client.post(RPC, json={
            "jsonrpc":"2.0",
            "method":"sendTransaction",
            "params":[tx]
        })

async def buy(mint, alpha):

    eng = pick_engine(alpha)

    if sum(1 for p in engine.positions if p["engine"]==eng) >= MAX_POSITION_PER_ENGINE:
        return False

    size = engine_size(eng, alpha)

    price = await get_price(mint)
    if not price:
        return False

    amount = size / price

    engine.positions.append({
        "token": mint,
        "amount": amount,
        "entry": price,
        "engine": eng,
        "alpha": alpha,
        "peak": price
    })

    print("BUY", mint[:6], eng, size)
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

    print("SELL", mint[:6], pnl)

# ================= MONITOR =================

async def monitor():

    while True:

        for p in list(engine.positions):

            price = await get_price(p["token"])
            if not price:
                continue

            p["peak"] = max(p["peak"], price)

            pnl_pct = (price - p["entry"]) / p["entry"]

            if pnl_pct > 0.15 or pnl_pct < -0.05:
                await sell(p)

        await asyncio.sleep(3)

# ================= MEMPOOL =================

async def handle_mempool(e):

    mint = e.get("mint")
    if not mint:
        return

    CANDIDATES.add(mint)

# ================= ALLOCATOR =================

def update_allocator():

    scores = {}

    for e in ENGINE_STATS:
        s = ENGINE_STATS[e]
        if s["trades"] == 0:
            scores[e] = 1
        else:
            win = s["wins"]/s["trades"]
            scores[e] = max(0.1, s["pnl"] * win)

    total = sum(scores.values()) + 1e-9

    for k in scores:
        ENGINE_ALLOCATOR[k] = scores[k]/total

# ================= MAIN =================

async def bot():

    asyncio.create_task(monitor())
    asyncio.create_task(mempool_stream(handle_mempool))

    while True:

        try:

            for mint in list(CANDIDATES):

                alpha = await momentum(mint)

                if alpha < 0.002:
                    continue

                await buy(mint, alpha)

            update_allocator()

        except Exception as e:
            print("ERR", e)

        await asyncio.sleep(2)

# ================= START =================

if __name__ == "__main__":
    asyncio.run(bot())
