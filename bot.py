# ================= v1000_GOD_MODE_ENGINE =================

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

MAX_DRAWDOWN = 0.25
LOSS_STREAK_LIMIT = 5

SEED_TOKENS = [
    SOL,
    "EPjFWdd5AufqSSqeM2q7KZ1xzy6h7Q5Gk1s7k9KkZx9"
]

# ================= INIT =================

if not hasattr(engine, "positions"):
    engine.positions = []

engine.logs = []
engine.capital = 1.0
engine.sol_balance = 1.0

engine.loss_streak = 0
engine.peak_capital = 1.0

# ================= ENGINE =================

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

MAX_POSITION_PER_ENGINE = 3

# ================= STATE =================

CANDIDATES = set(SEED_TOKENS)
LAST_PRICE = {}
LAST_SEEN = {}
FAILED_TOKENS = set()
TOKEN_COOLDOWN = defaultdict(float)

ALPHA_CACHE = {}

# ================= UTILS =================

def clamp(v, lo, hi):
    return max(lo, min(v, hi))

def now():
    return time.time()

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

async def multi_momentum(mint):
    prices = []
    for _ in range(3):
        p = await get_price(mint)
        if not p:
            return 0
        prices.append(p)
        await asyncio.sleep(0.08)

    return (prices[-1] - prices[0]) / prices[0]


async def flow_acceleration(mint):
    v1 = await multi_momentum(mint)
    await asyncio.sleep(0.1)
    v2 = await multi_momentum(mint)

    return max(0, v2 - v1)


async def volume_surge(mint):
    p = await get_price(mint)
    if not p:
        return 0

    prev = LAST_PRICE.get(mint, p)
    LAST_PRICE[mint] = p

    return abs(p - prev) / prev if prev > 0 else 0


async def alpha_confidence(alpha):
    """👉 防假訊號"""
    return 1 if alpha > 0.01 else 0


async def alpha_fusion(mint):

    if mint in ALPHA_CACHE and now() - ALPHA_CACHE[mint][1] < 2:
        return ALPHA_CACHE[mint][0]

    m = await multi_momentum(mint)
    f = await flow_acceleration(mint)
    v = await volume_surge(mint)

    score = (
        m * 0.4 +
        f * 0.3 +
        v * 0.3
    )

    ALPHA_CACHE[mint] = (score, now())

    return score

# ================= FILTER =================

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
        impact = float(r.json().get("priceImpactPct", 1))
        return impact < 0.35
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
        return int(r.json().get("outAmount", 0)) > 0
    except:
        return False

# ================= ENGINE =================

def pick_engine(alpha):
    if alpha > 0.05:
        return "sniper"
    return random.choices(
        ["stable","degen","sniper"],
        weights=list(ENGINE_ALLOCATOR.values())
    )[0]


def engine_size(name, alpha):
    base = ENGINE_BASE_SIZE[name]

    # 👉 連敗降低風險
    if engine.loss_streak >= 3:
        base *= 0.5

    return clamp(base * (1 + alpha*10), MIN_POSITION_SOL, MAX_POSITION_SOL)

# ================= EXEC =================

async def buy(mint, alpha):

    if now() - TOKEN_COOLDOWN[mint] < 15:
        return False

    if mint in FAILED_TOKENS:
        return False

    if any(p["token"] == mint for p in engine.positions):
        return False

    eng = pick_engine(alpha)

    if sum(1 for p in engine.positions if p.get("engine")==eng) >= MAX_POSITION_PER_ENGINE:
        return False

    confidence = await alpha_confidence(alpha)
    if confidence == 0:
        return False

    price = await get_price(mint)
    if not price:
        FAILED_TOKENS.add(mint)
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

    TOKEN_COOLDOWN[mint] = now()

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
        engine.loss_streak = 0
    else:
        engine.loss_streak += 1

    engine.positions.remove(pos)

    print(f"🔴 SELL {mint[:6]} pnl={round(pnl,6)}")

# ================= MONITOR =================

async def monitor():
    while True:
        for p in list(engine.positions):

            price = await get_price(p["token"])
            if not price:
                continue

            p["peak"] = max(p["peak"], price)

            pnl_pct = (price - p["entry"]) / p["entry"]

            if pnl_pct > 0.30 or pnl_pct < -0.10:
                await sell(p)

        await asyncio.sleep(2)

# ================= RISK =================

def risk_check():

    engine.peak_capital = max(engine.peak_capital, engine.capital)

    dd = (engine.peak_capital - engine.capital) / engine.peak_capital

    if dd > MAX_DRAWDOWN:
        print("🛑 KILL SWITCH (drawdown)")
        return False

    if engine.loss_streak >= LOSS_STREAK_LIMIT:
        print("🛑 KILL SWITCH (loss streak)")
        return False

    return True

# ================= MEMPOOL =================

async def handle_mempool(e):
    mint = e.get("mint")
    if mint:
        CANDIDATES.add(mint)
        LAST_SEEN[mint] = now()

# ================= CLEANUP =================

def cleanup():
    cutoff = now() - 600
    for m in list(CANDIDATES):
        if LAST_SEEN.get(m, now()) < cutoff:
            CANDIDATES.discard(m)

# ================= MAIN =================

async def bot():

    print("🚀 V1000 GOD MODE LIVE")

    asyncio.create_task(monitor())

    try:
        asyncio.create_task(mempool_stream(handle_mempool))
    except:
        pass

    while True:

        try:

            if not risk_check():
                await asyncio.sleep(5)
                continue

            cleanup()

            if len(CANDIDATES) < 3:
                CANDIDATES.update(SEED_TOKENS)

            for mint in list(CANDIDATES):

                alpha = await alpha_fusion(mint)

                threshold = 0.01 + random.uniform(0, 0.004)

                if alpha < threshold:
                    continue

                if not await liquidity_ok(mint):
                    continue

                if not await anti_rug(mint):
                    continue

                await buy(mint, alpha)

        except Exception as e:
            print("ERR", e)

        await asyncio.sleep(2)

# ================= ENTRY =================

async def bot_loop():
    await bot()

if __name__ == "__main__":
    asyncio.run(bot())
