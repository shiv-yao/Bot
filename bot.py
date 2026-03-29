# ================= FINAL_STABLE_BOT =================

import asyncio, time, random
from collections import defaultdict
import httpx

from state import engine
from mempool import mempool_stream

SOL = "So11111111111111111111111111111111111111112"

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
    "errors": 0
}

# ================= ENGINE =================

ENGINE_STATS = {
    "stable": {"pnl":0,"trades":0,"wins":0},
    "degen": {"pnl":0,"trades":0,"wins":0},
    "sniper": {"pnl":0,"trades":0,"wins":0},
}

ENGINE_ALLOCATOR = {
    "stable":0.4,
    "degen":0.4,
    "sniper":0.2,
}

ALPHA_MEMORY = {
    "stable":[],
    "degen":[],
    "sniper":[]
}

# ================= STATE =================

CANDIDATES = set()
TOKEN_COOLDOWN = defaultdict(float)
ALPHA_CACHE = {}
LAST_PRICE = {}

LAST_PUMP_ERROR = {"code": None, "ts": 0}

# ================= UTIL =================

def log(msg):
    engine.logs.append(msg)
    engine.logs = engine.logs[-200:]
    print(msg)

def valid_mint(m):
    return isinstance(m, str) and 32 <= len(m) <= 44

# ================= PRICE =================

async def get_price(mint):
    try:
        r = await HTTP.get(
            "https://lite-api.jup.ag/swap/v1/quote",
            params={
                "inputMint": mint,
                "outputMint": SOL,
                "amount": "1000000"
            }
        )
        out = int(r.json().get("outAmount", 0)) / 1e9
        return out / 1_000_000 if out > 0 else None
    except:
        engine.stats["errors"] += 1
        return None

# ================= TOKEN SOURCE =================

async def pump_scanner():
    while True:
        try:
            r = await HTTP.get(PUMP_API)

            if r.status_code != 200:
                now_ts = time.time()
                if (
                    LAST_PUMP_ERROR["code"] != r.status_code
                    or now_ts - LAST_PUMP_ERROR["ts"] > 60
                ):
                    log(f"PUMP_HTTP_{r.status_code}")
                    LAST_PUMP_ERROR["code"] = r.status_code
                    LAST_PUMP_ERROR["ts"] = now_ts

                await asyncio.sleep(8)
                continue

            data = r.json()

            for c in data[:20]:
                mint = c.get("mint")
                if valid_mint(mint):
                    CANDIDATES.add(mint)

        except Exception as e:
            log(f"PUMP_ERR {str(e)[:60]}")

        await asyncio.sleep(5)

async def handle_mempool(e):
    mint = e.get("mint")
    if valid_mint(mint):
        CANDIDATES.add(mint)

# ================= ALPHA =================

async def momentum(mint):
    p1 = await get_price(mint)
    await asyncio.sleep(0.1)
    p2 = await get_price(mint)

    if not p1 or not p2:
        return 0

    return (p2 - p1) / p1

async def volume_surge(mint):
    p = await get_price(mint)
    if not p:
        return 0

    prev = LAST_PRICE.get(mint, p)
    LAST_PRICE[mint] = p

    return abs(p - prev) / prev if prev > 0 else 0

async def alpha_engine(mint):

    if mint in ALPHA_CACHE and time.time() - ALPHA_CACHE[mint][1] < 2:
        return ALPHA_CACHE[mint][0]

    m = await momentum(mint)
    v = await volume_surge(mint)

    score = m * 0.6 + v * 0.4

    ALPHA_CACHE[mint] = (score, time.time())

    return score

# ================= ENGINE LOGIC =================

def update_allocator():
    weights = {}

    for k,v in ENGINE_STATS.items():
        if v["trades"] == 0:
            weights[k] = 1
        else:
            win = v["wins"]/max(v["trades"],1)
            weights[k] = (v["pnl"]+0.001)*win

    total = sum(abs(v) for v in weights.values()) + 1e-9
    for k in weights:
        weights[k] = abs(weights[k])/total

    ENGINE_ALLOCATOR.update(weights)

def pick_engine(alpha):
    if alpha > 0.05:
        return "sniper"
    return random.choices(
        ["stable","degen","sniper"],
        weights=list(ENGINE_ALLOCATOR.values())
    )[0]

def size(alpha, eng):
    base = MAX_POSITION_SOL * min(1, alpha*6)
    alloc = ENGINE_ALLOCATOR[eng]

    s = base * alloc

    if engine.loss_streak >= 3:
        s *= 0.5

    return max(MIN_POSITION_SOL, min(MAX_POSITION_SOL, s))

# ================= EXEC =================

def can_buy(mint):
    if len(engine.positions) >= MAX_POSITIONS:
        return False
    if any(p["token"] == mint for p in engine.positions):
        return False
    if time.time() - TOKEN_COOLDOWN[mint] < 10:
        return False
    return True

async def buy(mint, alpha):

    eng = pick_engine(alpha)

    if not can_buy(mint):
        return False

    price = await get_price(mint)
    if not price:
        return False

    s = size(alpha, eng)
    amount = s / price

    engine.positions.append({
        "token": mint,
        "amount": amount,
        "entry_price": price,
        "last_price": price,
        "peak_price": price,
        "pnl_pct": 0.0,
        "engine": eng
    })

    TOKEN_COOLDOWN[mint] = time.time()

    engine.stats["buys"] += 1
    engine.last_trade = f"BUY {mint[:6]}"

    log(f"BUY {mint[:6]} eng={eng} alpha={round(alpha,4)}")

    return True

async def sell(p):

    price = await get_price(p["token"])
    if not price:
        return

    pnl = (price - p["entry_price"]) * p["amount"]

    eng = p["engine"]

    ENGINE_STATS[eng]["trades"] += 1
    ENGINE_STATS[eng]["pnl"] += pnl

    if pnl > 0:
        ENGINE_STATS[eng]["wins"] += 1
        engine.loss_streak = 0
    else:
        engine.loss_streak += 1

    update_allocator()

    engine.capital += pnl

    engine.trade_history.append({
        "side": "SELL",
        "mint": p["token"],
        "result": {"pnl": pnl, "engine": eng}
    })

    engine.positions.remove(p)

    engine.stats["sells"] += 1
    engine.last_trade = f"SELL {p['token'][:6]}"

    log(f"SELL {p['token'][:6]} pnl={round(pnl,6)} eng={eng}")

# ================= MONITOR =================

async def monitor():
    while True:
        for p in list(engine.positions):

            price = await get_price(p["token"])
            if not price:
                continue

            p["last_price"] = price
            p["peak_price"] = max(p["peak_price"], price)

            pnl = (price - p["entry_price"]) / p["entry_price"]
            p["pnl_pct"] = pnl

            if pnl > 0.25 or pnl < -0.08:
                await sell(p)

        await asyncio.sleep(2)

# ================= MAIN =================

async def bot():

    log("BOT_STARTED")

    asyncio.create_task(monitor())
    asyncio.create_task(pump_scanner())

    try:
        asyncio.create_task(mempool_stream(handle_mempool))
    except:
        pass

    while True:

        try:

            if len(CANDIDATES) < 3:
                await asyncio.sleep(1)
                continue

            for mint in list(CANDIDATES):

                alpha = await alpha_engine(mint)

                engine.stats["signals"] += 1
                engine.last_signal = f"{mint[:6]} {round(alpha,4)}"

                if alpha < 0.01:
                    continue

                await buy(mint, alpha)

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"ERR {str(e)[:60]}")

        await asyncio.sleep(2)

# ================= ENTRY =================

async def bot_loop():
    await bot()

if __name__ == "__main__":
    asyncio.run(bot())
