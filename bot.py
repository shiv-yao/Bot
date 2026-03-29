# ================= v1200_FULL_CORE_ENGINE =================

import os, asyncio, time, random
from collections import defaultdict, deque
import httpx

from state import engine
from mempool import mempool_stream

RPC = os.getenv("RPC", "")
SOL = "So11111111111111111111111111111111111111112"
HELIUS_API = os.getenv("HELIUS_API", "")

MAX_POSITION_SOL = 0.0025
MIN_POSITION_SOL = 0.001
MAX_POSITIONS = 5

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

HTTP = httpx.AsyncClient(timeout=10)

# ================= STATE =================

CANDIDATES = set()
SMART_WALLETS = {}
WALLET_SCORES = defaultdict(float)
TOKEN_COOLDOWN = defaultdict(float)
ALPHA_CACHE = {}

# ================= LOG =================

def log(msg):
    engine.logs.append(msg)
    engine.logs = engine.logs[-200:]
    print(msg)

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

# ================= CANDIDATE =================

async def handle_mempool(e):
    mint = e.get("mint")
    if mint:
        CANDIDATES.add(mint)

# ================= SMART MONEY =================

async def detect_smart_wallets():
    """
    👉 真版應該接 Helius / wallet ranking
    """
    while True:
        try:
            # 模擬 wallet score（可替換）
            for i in range(5):
                wallet = f"wallet_{i}"
                SMART_WALLETS[wallet] = random.uniform(0.8, 1.2)

        except Exception as e:
            log(f"SMART_ERR {e}")

        await asyncio.sleep(10)

async def smart_wallet_signal(mint):
    """
    👉 模擬 smart money flow（未來換成 on-chain）
    """
    score = sum(SMART_WALLETS.values()) / (len(SMART_WALLETS) + 1)
    return score * random.uniform(0.8, 1.2)

# ================= ALPHA =================

async def momentum(mint):
    p1 = await get_price(mint)
    await asyncio.sleep(0.1)
    p2 = await get_price(mint)

    if not p1 or not p2:
        return 0

    return (p2 - p1) / p1

async def micro_structure(mint):
    p1 = await get_price(mint)
    await asyncio.sleep(0.05)
    p2 = await get_price(mint)
    await asyncio.sleep(0.05)
    p3 = await get_price(mint)

    if not p1 or not p2 or not p3:
        return 0

    return max(0, (p3 - p1) / p1)

async def alpha_engine(mint):

    if mint in ALPHA_CACHE and time.time() - ALPHA_CACHE[mint][1] < 2:
        return ALPHA_CACHE[mint][0]

    m = await momentum(mint)
    micro = await micro_structure(mint)
    smart = await smart_wallet_signal(mint)

    score = (
        m * 0.5 +
        micro * 0.3 +
        smart * 0.2
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
        impact = float(r.json().get("priceImpactPct", 1))
        return impact < 0.35
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

    if time.time() - TOKEN_COOLDOWN[mint] < 15:
        return False

    return True

def position_size(alpha):
    base = MAX_POSITION_SOL
    return max(MIN_POSITION_SOL, base * min(1.0, alpha * 8))

# ================= EXEC =================

async def buy(mint, alpha):

    if not can_buy(mint):
        return False

    price = await get_price(mint)
    if not price:
        return False

    size = position_size(alpha)
    amount = size / price

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

    log(f"BUY {mint[:6]} alpha={round(alpha,4)} size={round(size,6)}")

    return True

async def sell(pos):

    mint = pos["token"]

    price = await get_price(mint)
    if not price:
        return

    pnl = (price - pos["entry"]) * pos["amount"]

    engine.capital += pnl

    engine.trade_history.append({
        "mint": mint,
        "pnl": pnl
    })

    engine.positions.remove(pos)

    engine.stats["sells"] += 1
    engine.last_trade = f"SELL {mint}"

    log(f"SELL {mint[:6]} pnl={round(pnl,6)}")

# ================= MONITOR =================

async def monitor():

    while True:

        for p in list(engine.positions):

            price = await get_price(p["token"])
            if not price:
                continue

            p["peak"] = max(p["peak"], price)

            pnl_pct = (price - p["entry"]) / p["entry"]

            # 👉 多層 exit（核心）
            if pnl_pct > 0.25:
                await sell(p)
            elif pnl_pct < -0.08:
                await sell(p)
            elif p["peak"] > p["entry"] and (p["peak"] - price)/p["peak"] > 0.1:
                await sell(p)

        await asyncio.sleep(2)

# ================= MAIN =================

async def bot():

    log("🚀 FULL CORE ENGINE START")

    asyncio.create_task(monitor())
    asyncio.create_task(detect_smart_wallets())

    try:
        asyncio.create_task(mempool_stream(handle_mempool))
    except:
        pass

    while True:

        try:

            if len(CANDIDATES) < 5:
                # fallback
                CANDIDATES.add(SOL)

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

        await asyncio.sleep(2)

# ================= ENTRY =================

async def bot_loop():
    await bot()

if __name__ == "__main__":
    asyncio.run(bot())
