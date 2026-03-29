# ================= v1308 FULL MERGED BOT =================
import asyncio
import time
import random
from collections import defaultdict

import httpx

from state import engine
from mempool import mempool_stream

# ================= CONFIG =================
SOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wGk3Q3k5Jp3x"
USDT = "Es9vMFrzaCERm7w7z7y7v4JgJ6pG6fQ5gYdExgkt1Py"
BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6YaB1pPB263kzwc"
JUP = "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"

MAX_POSITION_SOL = 0.0025
MIN_POSITION_SOL = 0.001
MAX_POSITIONS = 5

PUMP_API = "https://frontend-api.pump.fun/coins/latest"
JUP_TOKENS_API = "https://token.jup.ag/all"

STATIC_UNIVERSE = {SOL, USDC, USDT, BONK, JUP}
FALLBACK_TOKENS = set(STATIC_UNIVERSE)

HTTP = httpx.AsyncClient(timeout=10.0)

# ================= AI PARAMS =================
AI_PARAMS = {
    "entry_threshold": 0.002,
    "size_multiplier": 1.0,
    "trailing_stop": 0.08,
}

# ================= STATE =================
CANDIDATES = set()
TOKEN_COOLDOWN = defaultdict(float)
PRICE_CACHE = {}
LAST_UNIVERSE_REFRESH = 0
LAST_LOG_TS = {}

# ================= UTIL =================
def now():
    return time.time()

def valid_mint(m):
    return isinstance(m, str) and 32 <= len(m) <= 44

def ensure_float(x, d=0):
    try:
        return float(x)
    except:
        return d

def log(msg):
    engine.logs.append(str(msg))
    engine.logs = engine.logs[-300:]
    print("[BOT]", msg)

def log_once(k, msg, cd=60):
    t = now()
    if t - LAST_LOG_TS.get(k, 0) > cd:
        LAST_LOG_TS[k] = t
        log(msg)

# ================= HTTP =================
async def http_get(url, params=None):
    try:
        r = await HTTP.get(url, params=params)
        if r.status_code != 200:
            return None
        return r.json()
    except:
        return None

# ================= MARKET =================
async def get_price(mint):
    if mint in PRICE_CACHE and now() - PRICE_CACHE[mint][1] < 4:
        return PRICE_CACHE[mint][0]

    data = await http_get(
        "https://lite-api.jup.ag/swap/v1/quote",
        {"inputMint": mint, "outputMint": SOL, "amount": "1000000"}
    )

    if not data:
        return None

    out = int(data.get("outAmount", 0))
    price = (out / 1e9) / 1_000_000 if out > 0 else None

    PRICE_CACHE[mint] = (price, now())
    return price

async def liquidity_ok(mint):
    data = await http_get(
        "https://lite-api.jup.ag/swap/v1/quote",
        {"inputMint": SOL, "outputMint": mint, "amount": "10000000"}
    )
    if not data:
        return False

    return int(data.get("outAmount", 0)) > 5000

async def anti_rug(mint):
    data = await http_get(
        "https://lite-api.jup.ag/swap/v1/quote",
        {"inputMint": mint, "outputMint": SOL, "amount": "1000000"}
    )
    return data and int(data.get("outAmount", 0)) > 0

# ================= SOURCES =================
async def add_candidate(m):
    if valid_mint(m) and m not in CANDIDATES:
        CANDIDATES.add(m)
        engine.stats["adds"] += 1

async def inject_fallback():
    for m in FALLBACK_TOKENS:
        await add_candidate(m)

async def pump():
    while True:
        data = await http_get(PUMP_API)
        if isinstance(data, list):
            for c in data[:20]:
                await add_candidate(c.get("mint"))
        else:
            await inject_fallback()
        await asyncio.sleep(10)

async def jup():
    while True:
        data = await http_get(JUP_TOKENS_API)
        if isinstance(data, list):
            random.shuffle(data)
            for t in data[:50]:
                await add_candidate(t.get("address"))
        await asyncio.sleep(120)

async def mempool_loop():
    while True:
        try:
            await mempool_stream(lambda e: add_candidate(e.get("mint")))
        except:
            await asyncio.sleep(5)

# ================= WALLET =================
def wallet_score(mint):
    return 1.0  # 保留你原本 + 可擴展

# ================= ALPHA =================
async def alpha(m):
    p1 = await get_price(m)
    await asyncio.sleep(1)
    p2 = await get_price(m)

    if not p1 or not p2:
        return 0

    return (p2 - p1) / p1

# ================= AI LOOP =================
async def ai_loop():
    while True:
        try:
            trades = engine.trade_history[-30:]
            if trades:
                avg = sum(t["pnl_pct"] for t in trades) / len(trades)

                if avg > 0:
                    AI_PARAMS["entry_threshold"] *= 0.98
                    AI_PARAMS["size_multiplier"] *= 1.05
                else:
                    AI_PARAMS["entry_threshold"] *= 1.05
                    AI_PARAMS["size_multiplier"] *= 0.95

                AI_PARAMS["entry_threshold"] = max(0.001, min(0.01, AI_PARAMS["entry_threshold"]))
                AI_PARAMS["size_multiplier"] = max(0.5, min(2.0, AI_PARAMS["size_multiplier"]))

        except Exception as e:
            log_once("ai", str(e))

        await asyncio.sleep(10)

# ================= EXEC =================
def can_buy(m):
    if m in {SOL, USDC, USDT}:
        return False
    if len(engine.positions) >= MAX_POSITIONS:
        return False
    if any(p["token"] == m for p in engine.positions):
        return False
    if now() - TOKEN_COOLDOWN[m] < 30:
        return False
    return True

async def buy(m, a):
    if not can_buy(m):
        return

    price = await get_price(m)
    if not price:
        return

    combo = a * wallet_score(m)
    size = MAX_POSITION_SOL * max(0.2, combo * 8) * AI_PARAMS["size_multiplier"]

    if combo > 0.02:
        size *= 1.3
    elif combo > 0.01:
        size *= 1.1

    size = max(MIN_POSITION_SOL, min(size, MAX_POSITION_SOL))

    engine.positions.append({
        "token": m,
        "entry_price": price,
        "last_price": price,
        "peak_price": price,
        "pnl_pct": 0,
        "amount": size / price,
        "engine": "degen",
        "alpha": a,
        "entry_ts": now()
    })

    TOKEN_COOLDOWN[m] = now()
    engine.stats["buys"] += 1
    engine.last_trade = f"BUY {m[:6]}"

    log(f"BUY {m[:6]} alpha={a:.4f} size={size:.6f}")

async def sell(p):
    price = await get_price(p["token"])
    if not price:
        return

    entry = p["entry_price"]
    pnl = (price - entry) / entry

    engine.positions.remove(p)

    engine.trade_history.append({
        "token": p["token"],
        "pnl_pct": pnl,
        "ts": now()
    })

    engine.stats["sells"] += 1
    log(f"SELL {p['token'][:6]} pnl={pnl:.4f}")

# ================= MONITOR =================
async def monitor():
    while True:
        for p in list(engine.positions):
            price = await get_price(p["token"])
            if not price:
                continue

            entry = p["entry_price"]
            pnl = (price - entry) / entry

            peak = max(p["peak_price"], price)
            p["peak_price"] = peak
            p["last_price"] = price
            p["pnl_pct"] = pnl

            drawdown = (price - peak) / peak if peak else 0
            stop = -AI_PARAMS["trailing_stop"]

            if pnl < stop or drawdown < stop:
                await sell(p)

        await asyncio.sleep(6)

# ================= MAIN =================
async def main():
    log("🚀 v1308 FULL BOT START")

    await inject_fallback()

    asyncio.create_task(pump())
    asyncio.create_task(jup())
    asyncio.create_task(mempool_loop())
    asyncio.create_task(monitor())
    asyncio.create_task(ai_loop())

    while True:
        try:
            if len(CANDIDATES) < 5:
                await inject_fallback()

            engine.candidate_count = len(CANDIDATES)

            for m in random.sample(list(CANDIDATES), min(15, len(CANDIDATES))):
                engine.stats["signals"] += 1

                if not await liquidity_ok(m):
                    continue

                if not await anti_rug(m):
                    continue

                a = await alpha(m)
                combo = a * wallet_score(m)

                engine.last_signal = f"{m[:6]} alpha={a:.4f} combo={combo:.4f}"

                if combo > AI_PARAMS["entry_threshold"]:
                    await buy(m, a)

            await asyncio.sleep(6)

        except Exception as e:
            engine.stats["errors"] += 1
            engine.bot_error = str(e)
            log(f"ERR {e}")
            await asyncio.sleep(5)

# ================= ENTRY =================
async def bot_loop():
    await main()
