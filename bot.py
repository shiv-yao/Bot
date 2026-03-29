# ================= v1305_LIVE_TRADABLE_ALPHA_BOT =================
import asyncio
import time
import random
from collections import defaultdict

import httpx

from state import engine
from mempool import mempool_stream
from wallet_tracker import wallet_tracker_loop, wallet_score

# ================= CONFIG =================
SOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wGk3Q3k5Jp3x"
USDT = "Es9vMFrzaCERm7w7z7y7v4JgJ6pG6fQ5gYdExgkt1Py"

MAX_POSITION_SOL = 0.003
MIN_POSITION_SOL = 0.001
MAX_POSITIONS = 5

PUMP_API = "https://frontend-api.pump.fun/coins/latest"
JUP_API = "https://token.jup.ag/all"

# 👉 先放幾個 wallet（很重要）
SMART_WALLETS = [
    "7YttLkHDoNj7sAt4EvsCzorBpvyEukgqS8bkkCm1cWg5",
]

HTTP = httpx.AsyncClient(timeout=10)

# ================= STATE =================
CANDIDATES = set()
TOKEN_COOLDOWN = defaultdict(float)
PRICE_CACHE = {}

# ================= UTIL =================
def now():
    return time.time()

def log(msg):
    engine.logs.append(str(msg))
    engine.logs = engine.logs[-200:]
    print("[BOT]", msg)

def valid_mint(m):
    return isinstance(m, str) and 32 <= len(m) <= 44

# ================= MARKET =================
async def get_price(mint):
    cached = PRICE_CACHE.get(mint)
    if cached and now() - cached[1] < 3:
        return cached[0]

    try:
        r = await HTTP.get(
            "https://lite-api.jup.ag/swap/v1/quote",
            params={"inputMint": mint, "outputMint": SOL, "amount": "1000000"},
        )
        data = r.json()
        out = int(data.get("outAmount", 0))
        price = (out / 1e9) / 1_000_000 if out > 0 else None
        PRICE_CACHE[mint] = (price, now())
        return price
    except:
        return None

# 🔥 放寬 liquidity（關鍵）
async def liquidity_ok(mint):
    try:
        r = await HTTP.get(
            "https://lite-api.jup.ag/swap/v1/quote",
            params={"inputMint": SOL, "outputMint": mint, "amount": "5000000"},
        )
        d = r.json()
        out = int(d.get("outAmount", 0))
        impact = float(d.get("priceImpactPct", 1))

        return out > 500 and impact < 0.85   # 🔥 放寬
    except:
        return False

async def anti_rug(mint):
    try:
        r = await HTTP.get(
            "https://lite-api.jup.ag/swap/v1/quote",
            params={"inputMint": mint, "outputMint": SOL, "amount": "1000000"},
        )
        return int(r.json().get("outAmount", 0)) > 0
    except:
        return False

# ================= TOKEN SOURCES =================
async def pump_scanner():
    while True:
        try:
            r = await HTTP.get(PUMP_API)
            data = r.json()

            for c in data[:20]:
                mint = c.get("mint")
                if valid_mint(mint):
                    CANDIDATES.add(mint)

            log(f"PUMP +{len(data[:20])}")
        except:
            log("PUMP_FAIL")

        await asyncio.sleep(8)

async def jup_scanner():
    while True:
        try:
            r = await HTTP.get(JUP_API)
            data = r.json()
            random.shuffle(data)

            for t in data[:50]:
                mint = t.get("address")
                if valid_mint(mint):
                    CANDIDATES.add(mint)

            log("JUP_OK")
        except:
            log("JUP_FAIL")

        await asyncio.sleep(120)

async def handle_mempool(e):
    mint = e.get("mint")
    if valid_mint(mint):
        CANDIDATES.add(mint)

# ================= STRATEGY =================
async def alpha(mint):
    p1 = await get_price(mint)
    await asyncio.sleep(1.0)
    p2 = await get_price(mint)

    if not p1 or not p2 or p1 <= 0:
        return 0

    return max(0, min(((p2 - p1) / p1) * 5, 0.15))  # 🔥 放大

def can_buy(mint):
    if mint in {SOL, USDC, USDT}:
        return False

    if len(engine.positions) >= MAX_POSITIONS:
        return False

    if any(p["token"] == mint for p in engine.positions):
        return False

    if now() - TOKEN_COOLDOWN[mint] < 20:
        return False

    return True

async def buy(mint, score):
    if not can_buy(mint):
        return False

    price = await get_price(mint)
    if not price:
        return False

    size = MAX_POSITION_SOL * min(1.0, score * 10)
    size = max(MIN_POSITION_SOL, size)

    engine.positions.append({
        "token": mint,
        "entry_price": price,
        "amount": size / price,
        "pnl_pct": 0,
        "engine": "sniper",
        "alpha": score,
    })

    engine.stats["buys"] += 1
    engine.last_trade = f"BUY {mint[:6]}"

    log(f"🔥 BUY {mint[:6]} score={score:.4f}")
    return True

async def sell(p):
    price = await get_price(p["token"])
    if not price:
        return

    entry = p["entry_price"]
    pnl = (price - entry) / entry

    engine.positions.remove(p)
    engine.stats["sells"] += 1

    log(f"SELL {p['token'][:6]} pnl={pnl:.2f}")

# ================= MONITOR =================
async def monitor():
    while True:
        for p in list(engine.positions):
            price = await get_price(p["token"])
            if not price:
                continue

            pnl = (price - p["entry_price"]) / p["entry_price"]

            if pnl > 0.25 or pnl < -0.12:
                await sell(p)

        await asyncio.sleep(5)

# ================= MAIN =================
async def main():
    log("🚀 v1305 LIVE BOT START")

    asyncio.create_task(pump_scanner())
    asyncio.create_task(jup_scanner())
    asyncio.create_task(mempool_stream(handle_mempool))
    asyncio.create_task(monitor())
    asyncio.create_task(wallet_tracker_loop(
        "https://api.mainnet-beta.solana.com",
        SMART_WALLETS,
        handle_mempool
    ))

    while True:
        try:
            candidates = list(CANDIDATES)
            random.shuffle(candidates)

            for mint in candidates[:20]:
                engine.stats["signals"] += 1

                if not await liquidity_ok(mint):
                    continue

                if not await anti_rug(mint):
                    continue

                a = await alpha(mint)
                w = wallet_score(mint)

                score = a + min(w * 0.02, 0.08)

                engine.last_signal = f"{mint[:6]} score={score:.4f}"

                # 🔥 超低門檻（一定會打）
                if score > 0.001:
                    await buy(mint, score)

            await asyncio.sleep(5)

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"ERR {e}")
            await asyncio.sleep(5)

# ================= ENTRY =================
async def bot_loop():
    await main()
