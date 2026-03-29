# ================= v1200_STABLE =================

import os, asyncio, time, random
from collections import defaultdict
import httpx

from state import engine
from mempool import mempool_stream

RPC = os.getenv("RPC", "")
SOL = "So11111111111111111111111111111111111111112"

MAX_POSITION_SOL = 0.0025
MIN_POSITION_SOL = 0.001
MAX_POSITIONS = 5

PUMP_API = "https://frontend-api.pump.fun/coins/latest"

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

# 🔥 單一 HTTP（關鍵）
HTTP = httpx.AsyncClient(timeout=10)

# ================= STATE =================

CANDIDATES = set([SOL])
TOKEN_COOLDOWN = defaultdict(float)
ALPHA_CACHE = {}
PRICE_CACHE = {}
LAST_PRICE = {}

# ================= LOG =================

def log(msg):
    engine.logs.append(msg)
    engine.logs = engine.logs[-200:]
    print(msg)

# ================= PRICE =================

async def get_price(mint):

    # 🔥 cache（減少 API）
    if mint in PRICE_CACHE and time.time() - PRICE_CACHE[mint][1] < 3:
        return PRICE_CACHE[mint][0]

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
        price = out / 1_000_000 if out > 0 else None

        PRICE_CACHE[mint] = (price, time.time())

        return price

    except:
        engine.stats["errors"] += 1
        return None

# ================= TOKEN =================

async def pump_scanner():
    while True:
        try:
            r = await HTTP.get(PUMP_API)
            data = r.json()

            for c in data[:15]:
                mint = c.get("mint")
                if mint:
                    CANDIDATES.add(mint)

        except:
            pass

        await asyncio.sleep(6)  # 🔥 降頻

async def handle_mempool(e):
    mint = e.get("mint")
    if mint:
        CANDIDATES.add(mint)

# ================= ALPHA =================

async def momentum(mint):
    p1 = await get_price(mint)
    await asyncio.sleep(0.08)
    p2 = await get_price(mint)

    if not p1 or not p2:
        return 0

    return (p2 - p1) / p1

async def micro(mint):
    p1 = await get_price(mint)
    await asyncio.sleep(0.05)
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

    # 🔥 cache（關鍵）
    if mint in ALPHA_CACHE and time.time() - ALPHA_CACHE[mint][1] < 4:
        return ALPHA_CACHE[mint][0]

    m = await momentum(mint)
    mic = await micro(mint)
    vol = await volume_surge(mint)

    score = (
        m * 0.5 +
        mic * 0.2 +
        vol * 0.3
    )

    ALPHA_CACHE[mint] = (score, time.time())

    return score

# ================= FILTER =================

async def liquidity_ok(mint):
    try:
        r = await HTTP.get(
            "https://lite-api.jup.ag/swap/v1/quote",
            params={
                "inputMint": SOL,
                "outputMint": mint,
                "amount": "10000000"
            }
        )
        return float(r.json().get("priceImpactPct", 1)) < 0.4
    except:
        return False

async def anti_rug(mint):
    try:
        r = await HTTP.get(
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

# ================= PORTFOLIO =================

def can_buy(mint):
    if len(engine.positions) >= MAX_POSITIONS:
        return False

    if any(p["token"] == mint for p in engine.positions):
        return False

    if time.time() - TOKEN_COOLDOWN[mint] < 20:
        return False

    return True

def size(alpha):
    return max(MIN_POSITION_SOL, MAX_POSITION_SOL * min(1, alpha * 6))

# ================= EXEC =================

async def buy(mint, alpha):

    if not can_buy(mint):
        return False

    price = await get_price(mint)
    if not price:
        return False

    s = size(alpha)
    amount = s / price

    engine.positions.append({
        "token": mint,
        "amount": amount,
        "entry": price,
        "alpha": alpha,
        "peak": price
    })

    TOKEN_COOLDOWN[mint] = time.time()

    engine.stats["buys"] += 1
    engine.last_trade = f"BUY {mint}"

    log(f"BUY {mint[:6]} alpha={round(alpha,4)}")

    return True

async def sell(p):

    price = await get_price(p["token"])
    if not price:
        return

    pnl = (price - p["entry"]) * p["amount"]

    engine.capital += pnl

    engine.trade_history.append({
        "mint": p["token"],
        "pnl": pnl
    })

    engine.positions.remove(p)

    engine.stats["sells"] += 1
    engine.last_trade = f"SELL {p['token']}"

    log(f"SELL {p['token'][:6]} pnl={round(pnl,6)}")

# ================= MONITOR =================

async def monitor():
    while True:

        for p in list(engine.positions):

            price = await get_price(p["token"])
            if not price:
                continue

            p["peak"] = max(p["peak"], price)

            pnl = (price - p["entry"]) / p["entry"]

            if pnl > 0.25 or pnl < -0.08:
                await sell(p)

        await asyncio.sleep(3)  # 🔥 降頻

# ================= MAIN =================

async def bot():

    log("🚀 FULL IMPLEMENTATION STABLE")

    asyncio.create_task(monitor())
    asyncio.create_task(pump_scanner())

    try:
        asyncio.create_task(mempool_stream(handle_mempool))
    except:
        pass

    while True:

        try:

            # 🔥 fallback
            if len(CANDIDATES) < 3:
                CANDIDATES.add(SOL)
                await asyncio.sleep(1)
                continue

            for mint in list(CANDIDATES):

                alpha = await alpha_engine(mint)

                engine.stats["signals"] += 1
                engine.last_signal = f"{mint[:6]} {round(alpha,4)}"

                if alpha < 0.01:
                    continue

                if not await liquidity_ok(mint):
                    continue

                if not await anti_rug(mint):
                    continue

                await buy(mint, alpha)

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"ERR {e}")

        # 🔥 關鍵：避免 Railway kill
        await asyncio.sleep(3 + random.random()*2)

# ================= ENTRY =================

async def bot_loop():
    await bot()

if __name__ == "__main__":
    asyncio.run(bot())
