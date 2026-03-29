# ================= v1000_PRO_STABLE =================

import os, asyncio, random, time
from collections import defaultdict
import httpx

from state import engine
from mempool import mempool_stream

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

PUMP_API = "https://frontend-api.pump.fun/coins/latest"

# ================= INIT =================

if not hasattr(engine, "positions"):
    engine.positions = []

engine.logs = []
engine.trade_history = []
engine.capital = 1.0
engine.sol_balance = 1.0

engine.loss_streak = 0
engine.peak_capital = 1.0

# ✅ 單例 HTTP（修復）
HTTP = httpx.AsyncClient(timeout=10)

# ================= STATE =================

CANDIDATES = set(SEED_TOKENS)
LAST_PRICE = {}
LAST_SEEN = {}
FAILED_TOKENS = set()
TOKEN_COOLDOWN = defaultdict(float)
ALPHA_CACHE = {}

# ================= UTILS =================

def log(msg):
    engine.logs.append(msg)
    engine.logs = engine.logs[-200:]
    print(msg)

def clamp(v, lo, hi):
    return max(lo, min(v, hi))

def now():
    return time.time()

# ================= PUMP =================

async def fetch_new_tokens():
    try:
        r = await HTTP.get(PUMP_API)
        data = r.json()
        return [c.get("mint") for c in data if c.get("mint")][:20]
    except:
        return []

async def pump_scanner():
    while True:
        tokens = await fetch_new_tokens()
        for mint in tokens:
            if mint and mint not in CANDIDATES:
                CANDIDATES.add(mint)
                LAST_SEEN[mint] = now()
                log(f"🆕 NEW {mint[:6]}")
        await asyncio.sleep(5)

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
        return None

# ================= ALPHA =================

async def multi_momentum(mint):
    prices = []
    for _ in range(3):
        p = await get_price(mint)
        if not p:
            return 0
        prices.append(p)
        await asyncio.sleep(0.06)
    return (prices[-1] - prices[0]) / prices[0]

async def flow_acceleration(mint):
    v1 = await multi_momentum(mint)
    await asyncio.sleep(0.08)
    v2 = await multi_momentum(mint)
    return max(0, v2 - v1)

async def volume_surge(mint):
    p = await get_price(mint)
    if not p:
        return 0
    prev = LAST_PRICE.get(mint, p)
    LAST_PRICE[mint] = p
    return abs(p - prev) / prev if prev > 0 else 0

async def alpha_fusion(mint):
    if mint in ALPHA_CACHE and now() - ALPHA_CACHE[mint][1] < 2:
        return ALPHA_CACHE[mint][0]

    m = await multi_momentum(mint)
    f = await flow_acceleration(mint)
    v = await volume_surge(mint)

    score = (m*0.4 + f*0.3 + v*0.3)
    ALPHA_CACHE[mint] = (score, now())
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
        return float(r.json().get("priceImpactPct", 1)) < 0.35
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

# ================= EXEC =================

async def buy(mint, alpha):

    if now() - TOKEN_COOLDOWN[mint] < 15:
        return False

    if mint in FAILED_TOKENS:
        return False

    if any(p["token"] == mint for p in engine.positions):
        return False

    price = await get_price(mint)
    if not price:
        FAILED_TOKENS.add(mint)
        return False

    size = clamp(0.002 * (1 + alpha*8), MIN_POSITION_SOL, MAX_POSITION_SOL)
    amount = size / price

    engine.positions.append({
        "token": mint,
        "amount": amount,
        "entry": price,
        "alpha": alpha,
        "peak": price
    })

    TOKEN_COOLDOWN[mint] = now()

    log(f"🟢 BUY {mint[:6]} α={round(alpha,5)}")
    return True


async def sell(pos):

    mint = pos["token"]
    price = await get_price(mint)
    if not price:
        return

    pnl = (price - pos["entry"]) * pos["amount"]

    engine.capital += pnl

    if pnl > 0:
        engine.loss_streak = 0
    else:
        engine.loss_streak += 1

    engine.trade_history.append({
        "mint": mint,
        "pnl": pnl
    })
    engine.trade_history = engine.trade_history[-200:]

    engine.positions.remove(pos)

    log(f"🔴 SELL {mint[:6]} pnl={round(pnl,6)}")

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

# ================= RISK =================

def risk_check():
    engine.peak_capital = max(engine.peak_capital, engine.capital)
    dd = (engine.peak_capital - engine.capital) / engine.peak_capital

    if dd > MAX_DRAWDOWN:
        log("🛑 DRAWDOWN STOP")
        return False

    if engine.loss_streak >= LOSS_STREAK_LIMIT:
        log("🛑 LOSS STREAK STOP")
        return False

    return True

# ================= MAIN =================

async def bot():

    log("🚀 V1000 PRO STABLE")

    asyncio.create_task(monitor())
    asyncio.create_task(pump_scanner())

    try:
        asyncio.create_task(mempool_stream(lambda e: CANDIDATES.add(e.get("mint"))))
    except:
        pass

    while True:

        try:

            if not risk_check():
                await asyncio.sleep(5)
                continue

            if len(CANDIDATES) < 3:
                CANDIDATES.update(SEED_TOKENS)

            for mint in list(CANDIDATES):

                alpha = await alpha_fusion(mint)

                if alpha < 0.01:
                    continue

                if not await liquidity_ok(mint):
                    continue

                if not await anti_rug(mint):
                    continue

                await buy(mint, alpha)

        except Exception as e:
            log(f"ERR {e}")

        await asyncio.sleep(2)

# ================= ENTRY =================

async def bot_loop():
    await bot()

if __name__ == "__main__":
    asyncio.run(bot())
