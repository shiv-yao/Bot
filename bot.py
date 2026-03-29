# ================= FINAL_MERGED_BOT_STABLE =================

import asyncio
import time
import random
from collections import defaultdict

import httpx

from state import engine
from mempool import mempool_stream

SOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

MAX_POSITION_SOL = 0.0025
MIN_POSITION_SOL = 0.001
MAX_POSITIONS = 5

PUMP_API = "https://frontend-api.pump.fun/coins/latest"

HTTP = httpx.AsyncClient(timeout=10)

# ================= INIT =================

if not hasattr(engine, "positions"):
    engine.positions = []

engine.logs = []
engine.trade_history = []
engine.capital = 1.0
engine.loss_streak = 0
engine.last_trade = ""
engine.last_signal = ""

engine.stats = {
    "signals": 0,
    "buys": 0,
    "sells": 0,
    "errors": 0,
}

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

ALPHA_MEMORY = {
    "stable": [],
    "degen": [],
    "sniper": [],
}

# ================= STATE =================

SEED_TOKENS = {SOL, USDC}

CANDIDATES = set()
TOKEN_COOLDOWN = defaultdict(float)
ALPHA_CACHE = {}
LAST_PRICE = {}
PRICE_CACHE = {}

# ================= LOG =================

def log(msg: str) -> None:
    engine.logs.append(msg)
    engine.logs = engine.logs[-200:]
    print(msg)

# ================= PRICE =================

async def get_price(mint: str):
    cached = PRICE_CACHE.get(mint)
    if cached and time.time() - cached[1] < 3:
        return cached[0]

    try:
        r = await HTTP.get(
            "https://lite-api.jup.ag/swap/v1/quote",
            params={
                "inputMint": mint,
                "outputMint": SOL,
                "amount": "1000000",
            },
        )

        if r.status_code != 200:
            return None

        data = r.json()
        out_amount = int(data.get("outAmount", 0) or 0)
        price = (out_amount / 1e9) / 1_000_000 if out_amount > 0 else None

        PRICE_CACHE[mint] = (price, time.time())
        return price

    except Exception:
        engine.stats["errors"] += 1
        return None

# ================= TOKEN =================

async def pump_scanner():
    while True:
        try:
            r = await HTTP.get(PUMP_API)

            if r.status_code != 200:
                log(f"PUMP_HTTP_{r.status_code}")
                await asyncio.sleep(8)
                continue

            text = r.text.strip()
            if not text:
                log("PUMP_EMPTY")
                await asyncio.sleep(8)
                continue

            try:
                data = r.json()
            except Exception:
                log(f"PUMP_BAD_JSON {text[:120]}")
                await asyncio.sleep(8)
                continue

            if not isinstance(data, list):
                log(f"PUMP_BAD_SHAPE {type(data).__name__}")
                await asyncio.sleep(8)
                continue

            added = 0
            for c in data[:20]:
                mint = c.get("mint") if isinstance(c, dict) else None
                if mint:
                    if mint not in CANDIDATES:
                        added += 1
                    CANDIDATES.add(mint)

            log(f"PUMP_OK added={added} total={len(CANDIDATES)}")

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"PUMP_ERR {e}")

        await asyncio.sleep(8)

async def handle_mempool(e: dict):
    mint = e.get("mint")
    if mint:
        CANDIDATES.add(mint)

# ================= ALPHA =================

async def momentum(mint: str) -> float:
    p1 = await get_price(mint)
    await asyncio.sleep(0.1)
    p2 = await get_price(mint)

    if not p1 or not p2 or p1 <= 0:
        return 0.0

    return (p2 - p1) / p1

async def volume_surge(mint: str) -> float:
    p = await get_price(mint)
    if not p:
        return 0.0

    prev = LAST_PRICE.get(mint, p)
    LAST_PRICE[mint] = p

    return abs(p - prev) / prev if prev > 0 else 0.0

async def alpha_engine(mint: str) -> float:
    cached = ALPHA_CACHE.get(mint)
    if cached and time.time() - cached[1] < 4:
        return cached[0]

    m = await momentum(mint)
    v = await volume_surge(mint)

    score = m * 0.6 + v * 0.4

    ALPHA_CACHE[mint] = (score, time.time())
    return score

# ================= ENGINE LOGIC =================

def update_allocator() -> None:
    weights = {}

    for k, v in ENGINE_STATS.items():
        if v["trades"] == 0:
            weights[k] = 1.0
        else:
            winrate = v["wins"] / max(v["trades"], 1)
            weights[k] = max(0.0, v["pnl"] + 0.001) * winrate

    total = sum(abs(v) for v in weights.values()) + 1e-9

    for k in weights:
        weights[k] = abs(weights[k]) / total

    ENGINE_ALLOCATOR.update(weights)

def get_alpha_edge(engine_name: str, alpha: float) -> float:
    mem = ALPHA_MEMORY[engine_name]
    if not mem:
        return 1.0

    similar = [pnl for a, pnl in mem if abs(a - alpha) < 0.02]
    if not similar:
        return 1.0

    avg = sum(similar) / len(similar)
    return max(0.5, min(2.0, 1 + avg * 5))

def pick_engine(alpha: float) -> str:
    if alpha > 0.05:
        return "sniper"

    return random.choices(
        ["stable", "degen", "sniper"],
        weights=list(ENGINE_ALLOCATOR.values()),
        k=1,
    )[0]

def size(alpha: float, eng: str) -> float:
    base = MAX_POSITION_SOL * min(1.0, alpha * 6)
    edge = get_alpha_edge(eng, alpha)
    alloc = ENGINE_ALLOCATOR[eng]

    s = base * edge * alloc

    if engine.loss_streak >= 3:
        s *= 0.5

    return max(MIN_POSITION_SOL, min(MAX_POSITION_SOL, s))

# ================= EXEC =================

def can_buy(mint: str) -> bool:
    if len(engine.positions) >= MAX_POSITIONS:
        return False

    if any(p["token"] == mint for p in engine.positions):
        return False

    if time.time() - TOKEN_COOLDOWN[mint] < 10:
        return False

    return True

async def buy(mint: str, alpha: float) -> bool:
    eng = pick_engine(alpha)

    if not can_buy(mint):
        return False

    price = await get_price(mint)
    if not price or price <= 0:
        return False

    s = size(alpha, eng)
    amount = s / price

    engine.positions.append(
        {
            "token": mint,
            "amount": amount,
            "entry": price,
            "alpha": alpha,
            "engine": eng,
            "peak": price,
            "last_price": price,
        }
    )

    TOKEN_COOLDOWN[mint] = time.time()

    engine.stats["buys"] += 1
    engine.last_trade = f"BUY {mint[:8]}"

    log(f"BUY {mint[:8]} eng={eng} alpha={round(alpha, 4)} size={round(s, 6)}")
    return True

async def sell(p: dict) -> None:
    price = await get_price(p["token"])
    if not price:
        return

    pnl = (price - p["entry"]) * p["amount"]
    eng = p["engine"]

    ENGINE_STATS[eng]["trades"] += 1
    ENGINE_STATS[eng]["pnl"] += pnl

    if pnl > 0:
        ENGINE_STATS[eng]["wins"] += 1
        engine.loss_streak = 0
    else:
        engine.loss_streak += 1

    ALPHA_MEMORY[eng].append((p["alpha"], pnl))
    ALPHA_MEMORY[eng] = ALPHA_MEMORY[eng][-100:]

    update_allocator()

    engine.capital += pnl

    engine.trade_history.append(
        {
            "mint": p["token"],
            "pnl": pnl,
            "engine": eng,
        }
    )
    engine.trade_history = engine.trade_history[-200:]

    if p in engine.positions:
        engine.positions.remove(p)

    engine.stats["sells"] += 1
    engine.last_trade = f"SELL {p['token'][:8]}"

    log(f"SELL {p['token'][:8]} pnl={round(pnl, 6)} eng={eng}")

# ================= MONITOR =================

async def monitor():
    while True:
        try:
            for p in list(engine.positions):
                price = await get_price(p["token"])
                if not price:
                    continue

                p["last_price"] = price
                p["peak"] = max(p["peak"], price)

                pnl_ratio = (price - p["entry"]) / p["entry"]

                # fixed TP/SL
                if pnl_ratio > 0.25 or pnl_ratio < -0.08:
                    await sell(p)
                    continue

                # trailing stop
                if p["peak"] > p["entry"]:
                    dd_from_peak = (p["peak"] - price) / p["peak"]
                    if dd_from_peak > 0.10:
                        await sell(p)
                        continue

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"MONITOR_ERR {e}")

        await asyncio.sleep(2)

# ================= MAIN =================

async def bot():
    log("FINAL_BOT_LIVE")

    asyncio.create_task(monitor())
    asyncio.create_task(pump_scanner())

    try:
        asyncio.create_task(mempool_stream(handle_mempool))
    except Exception as e:
        log(f"MEMPOOL_DISABLED {e}")

    while True:
        try:
            if len(CANDIDATES) == 0:
                CANDIDATES.update(SEED_TOKENS)
                log("SEED_BOOTSTRAP")

            if len(CANDIDATES) < 3:
                CANDIDATES.update(SEED_TOKENS)
                log("FALLBACK_ADD_SEEDS")
                await asyncio.sleep(2)
                continue

            for mint in list(CANDIDATES):
                alpha = await alpha_engine(mint)

                engine.stats["signals"] += 1
                engine.last_signal = f"{mint[:8]} {round(alpha, 4)}"

                if alpha < 0.01:
                    continue

                await buy(mint, alpha)

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"ERR {e}")

        await asyncio.sleep(2)

# ================= ENTRY =================

async def bot_loop():
    await bot()

if __name__ == "__main__":
    asyncio.run(bot_loop())
