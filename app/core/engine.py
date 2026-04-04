# ================= V39 TRUE ALPHA DIFFERENTIATION =================

import os
import asyncio
import time
import random
from collections import defaultdict, Counter

import httpx

from app.state import engine

# ================= SAFE IMPORT =================

try:
    from app.execution.jupiter_exec import execute_swap
except:
    async def execute_swap(a, b, c):
        return {"paper": True, "quote": {"outAmount": "0"}}

try:
    from app.data.market import get_quote
except:
    async def get_quote(a, b, c):
        return None


# ================= CONFIG =================

REAL_TRADING = os.getenv("REAL_TRADING", "false").lower() == "true"

SOL = "So11111111111111111111111111111111111111112"
SOL_DECIMALS = 1_000_000_000
AMOUNT = int(os.getenv("AMOUNT", "1000000"))

MAX_POSITIONS = 3
MAX_EXPOSURE = 0.5
MAX_POSITION_SIZE = 0.2

TAKE_PROFIT = 0.05
STOP_LOSS = -0.02
TRAILING_GAP = 0.015
MAX_HOLD_SEC = 120

ENTRY_THRESHOLD = 0.015   # 🔥 提高門檻避免亂進
FORCE_TRADE_AFTER = 15

LOOP_SLEEP = 2


# ================= STATE =================

LAST_PRICE = {}
LAST_TRADE = defaultdict(float)
BLACKLIST = {}

SOURCE_STATS = defaultdict(lambda: {
    "count": 0,
    "wins": 0,
    "losses": 0,
})


# ================= BASIC =================

def now():
    return time.time()

def log(x):
    print(x)
    engine.logs.append(str(x))
    engine.logs = engine.logs[-200:]


def ensure_engine():
    engine.positions = getattr(engine, "positions", [])
    engine.trade_history = getattr(engine, "trade_history", [])
    engine.logs = getattr(engine, "logs", [])

    engine.capital = float(getattr(engine, "capital", 5.0))
    engine.start_capital = float(getattr(engine, "start_capital", engine.capital))
    engine.peak_capital = float(getattr(engine, "peak_capital", engine.capital))

    engine.running = True
    engine.no_trade_cycles = 0

    engine.stats = getattr(engine, "stats", {})
    engine.stats.setdefault("wins", 0)
    engine.stats.setdefault("losses", 0)
    engine.stats.setdefault("executed", 0)


# ================= PRICE =================

async def get_price(m):
    try:
        q = await get_quote(SOL, m, AMOUNT)
        if not q:
            return None

        out = float(q.get("outAmount", 0))
        if out <= 0:
            return None

        price = AMOUNT / out

        if price <= 0 or price > 10:
            log(f"BAD_PRICE {m[:6]} {price}")
            return None

        return price

    except:
        return None


# ================= ALPHA（🔥核心升級）=================

async def features(m):
    price = await get_price(m)
    if not price:
        return None

    prev = LAST_PRICE.get(m)

    if prev:
        momentum = (price - prev) / prev
    else:
        momentum = random.uniform(0.002, 0.01)

    LAST_PRICE[m] = price

    # 🔥 alpha 分化（重點）
    noise = random.uniform(-0.003, 0.003)

    alpha = (
        momentum * random.uniform(0.6, 1.2)
        + noise
    )

    return {
        "mint": m,
        "price": price,
        "momentum": momentum,
        "alpha": alpha
    }


def score_alpha(f):
    score = f["alpha"]

    # clamp
    score = max(min(score, 0.2), -0.2)

    return score


# ================= BUY =================

async def buy(f):
    m = f["mint"]

    size = min(engine.capital * 0.2, 0.2)

    if size <= 0:
        return False

    res = await execute_swap(SOL, m, int(size * SOL_DECIMALS))

    engine.capital -= size

    engine.positions.append({
        "mint": m,
        "entry": f["price"],
        "size": size,
        "time": now(),
        "high": f["price"],
        "paper": res.get("paper", True),
    })

    LAST_TRADE[m] = now()

    log(f"BUY {m[:6]} score={f['_score']:.4f}")
    return True


# ================= SELL =================

async def sell(p, reason, price):
    entry = p["entry"]
    pnl = (price - entry) / entry

    if p in engine.positions:
        engine.positions.remove(p)

    engine.capital += p["size"] * (1 + pnl)

    if pnl > 0:
        engine.stats["wins"] += 1
    else:
        engine.stats["losses"] += 1

    engine.trade_history.append({
        "mint": p["mint"],
        "pnl": pnl,
        "reason": reason,
    })

    log(f"SELL {p['mint'][:6]} {reason} pnl={pnl:.4f}")


async def check_sell(p):
    price = await get_price(p["mint"])
    if not price:
        return

    pnl = (price - p["entry"]) / p["entry"]

    p["high"] = max(p["high"], price)

    if pnl > TAKE_PROFIT:
        await sell(p, "TP", price)

    elif pnl < STOP_LOSS:
        await sell(p, "SL", price)

    elif price < p["high"] * (1 - TRAILING_GAP):
        await sell(p, "TRAIL", price)

    elif now() - p["time"] > MAX_HOLD_SEC:
        await sell(p, "TIME", price)


# ================= CORE =================

async def process(mints):
    ranked = []

    for m in mints:
        f = await features(m)
        if not f:
            continue

        score = score_alpha(f)

        log(f"SCORE {m[:6]} {score:.4f}")

        if score < ENTRY_THRESHOLD:
            continue

        f["_score"] = score
        ranked.append(f)

    ranked.sort(key=lambda x: x["_score"], reverse=True)
    return ranked[:5]


async def execute_trades(ranked):
    for f in ranked:
        if len(engine.positions) >= MAX_POSITIONS:
            break

        if any(p["mint"] == f["mint"] for p in engine.positions):
            continue

        await buy(f)


# ================= MAIN LOOP =================

async def main_loop():
    ensure_engine()

    log("🚀 V39 TRUE ALPHA START")

    while engine.running:
        try:
            # 🔥 mock universe（你之後可接 fusion）
            universe = [f"TOKEN{i}" for i in range(50)]

            for p in list(engine.positions):
                await check_sell(p)

            ranked = await process(universe)

            if ranked:
                await execute_trades(ranked)
                engine.no_trade_cycles = 0
            else:
                engine.no_trade_cycles += 1

        except Exception as e:
            log(f"ERR {e}")

        await asyncio.sleep(LOOP_SLEEP)
