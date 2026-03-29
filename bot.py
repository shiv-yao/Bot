# ================= v1303_1_ANTI_BLOCK_BOT =================
import asyncio
import time
import random
import traceback
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

HTTP = httpx.AsyncClient(timeout=10.0, follow_redirects=True)

# ================= STATE =================
CANDIDATES = set()
TOKEN_COOLDOWN = defaultdict(float)

PRICE_CACHE = {}
ALPHA_CACHE = {}

LAST_UNIVERSE_REFRESH = 0
PUMP_FAILS = 0
JUP_FAILS = 0
MEMPOOL_FAILS = 0

LAST_LOG_TS = {}

# ================= UTIL =================
def now():
    return time.time()

def valid_mint(m):
    return isinstance(m, str) and 32 <= len(m) <= 44

def ensure_list(x):
    return x if isinstance(x, list) else []

def ensure_dict(x):
    return x if isinstance(x, dict) else {}

def ensure_float(x, d=0):
    try:
        return float(x)
    except:
        return d

def ensure_int(x, d=0):
    try:
        return int(x)
    except:
        return d

def safe_slice(x, n):
    return list(x[:n]) if isinstance(x, (list, tuple)) else []

def log(msg):
    engine.logs.append(str(msg))
    engine.logs = engine.logs[-300:]
    print("[BOT]", msg)

def log_once(key, msg, cooldown=60):
    t = now()
    if t - LAST_LOG_TS.get(key, 0) > cooldown:
        LAST_LOG_TS[key] = t
        log(msg)

def repair():
    engine.positions = ensure_list(getattr(engine, "positions", []))
    engine.logs = ensure_list(getattr(engine, "logs", []))
    engine.trade_history = ensure_list(getattr(engine, "trade_history", []))

    s = ensure_dict(getattr(engine, "stats", {}))
    engine.stats = {
        "signals": ensure_int(s.get("signals")),
        "buys": ensure_int(s.get("buys")),
        "sells": ensure_int(s.get("sells")),
        "errors": ensure_int(s.get("errors")),
        "adds": ensure_int(s.get("adds")),
    }

# ================= HTTP =================
async def http_get_json(url, params=None):
    try:
        r = await HTTP.get(url, params=params)
        if r.status_code != 200:
            return None, r.status_code
        return r.json(), 200
    except:
        return None, None

# ================= MARKET =================
async def get_price(mint):
    if not valid_mint(mint):
        return None

    c = PRICE_CACHE.get(mint)
    if c and now() - c[1] < 5:
        return c[0]

    data, status = await http_get_json(
        "https://lite-api.jup.ag/swap/v1/quote",
        {"inputMint": mint, "outputMint": SOL, "amount": "1000000"}
    )

    if status != 200 or not isinstance(data, dict):
        return None

    out = ensure_int(data.get("outAmount"))
    price = (out / 1e9) / 1_000_000 if out > 0 else None

    PRICE_CACHE[mint] = (price, now())
    return price

async def liquidity_ok(mint):
    data, _ = await http_get_json(
        "https://lite-api.jup.ag/swap/v1/quote",
        {"inputMint": SOL, "outputMint": mint, "amount": "10000000"}
    )
    if not isinstance(data, dict):
        return False

    return ensure_int(data.get("outAmount")) > 5000

async def anti_rug(mint):
    data, _ = await http_get_json(
        "https://lite-api.jup.ag/swap/v1/quote",
        {"inputMint": mint, "outputMint": SOL, "amount": "1000000"}
    )
    return isinstance(data, dict) and ensure_int(data.get("outAmount")) > 0

# ================= TOKEN =================
async def add_candidate(mint):
    if valid_mint(mint):
        CANDIDATES.add(mint)

async def inject_fallback():
    for m in FALLBACK_TOKENS:
        await add_candidate(m)

async def pump_scanner():
    global PUMP_FAILS

    while True:
        data, status = await http_get_json(PUMP_API)

        if status != 200 or not isinstance(data, list):
            PUMP_FAILS += 1
            log_once("pump", f"PUMP_HTTP_{status}")
            await inject_fallback()
            await asyncio.sleep(min(10 * PUMP_FAILS, 120))
            continue

        PUMP_FAILS = 0

        for c in safe_slice(data, 20):
            if isinstance(c, dict):
                await add_candidate(c.get("mint"))

        await asyncio.sleep(10)

async def jup_scanner():
    global JUP_FAILS

    while True:
        data, status = await http_get_json(JUP_TOKENS_API)

        if status != 200 or not isinstance(data, list):
            JUP_FAILS += 1
            log_once("jup", f"JUP_ERR {status}")
            await asyncio.sleep(min(30 * JUP_FAILS, 300))
            continue

        JUP_FAILS = 0

        random.shuffle(data)

        for t in safe_slice(data, 50):
            await add_candidate(t.get("address"))

        await asyncio.sleep(180)

async def mempool_runner():
    global MEMPOOL_FAILS

    while True:
        try:
            await mempool_stream(lambda e: add_candidate(e.get("mint")))
            MEMPOOL_FAILS = 0
        except Exception as e:
            MEMPOOL_FAILS += 1
            msg = str(e)

            if "429" in msg:
                log_once("mp429", "MEMPOOL 429 BLOCK")
                await asyncio.sleep(min(60 * MEMPOOL_FAILS, 600))
            else:
                log_once("mp", msg)
                await asyncio.sleep(min(5 * MEMPOOL_FAILS, 120))

# ================= STRATEGY =================
async def alpha(mint):
    p1 = await get_price(mint)
    await asyncio.sleep(0.1)
    p2 = await get_price(mint)

    if not p1 or not p2:
        return 0

    return max(0, min((p2 - p1) / p1 * 0.6, 0.08))

def can_buy(mint):
    if mint in {SOL, USDC, USDT}:
        return False
    if len(engine.positions) >= MAX_POSITIONS:
        return False
    return True

async def buy(mint, a):
    if not can_buy(mint):
        return

    price = await get_price(mint)
    if not price:
        return

    size = MAX_POSITION_SOL * max(0.2, a * 8)

    engine.positions.append({
        "token": mint,
        "entry_price": price,
        "amount": size / price,
        "engine": "degen",
        "alpha": a
    })

    engine.stats["buys"] += 1
    log(f"BUY {mint[:6]} alpha={a:.3f}")

async def monitor():
    while True:
        for p in list(engine.positions):
            price = await get_price(p["token"])
            if not price:
                continue

            pnl = (price - p["entry_price"]) / p["entry_price"]

            if pnl > 0.18 or pnl < -0.1:
                engine.positions.remove(p)
                engine.stats["sells"] += 1
                log(f"SELL {p['token'][:6]} pnl={pnl:.2f}")

        await asyncio.sleep(6)

# ================= MAIN =================
async def main():
    log("🚀 v1303.1 BOT START")

    await inject_fallback()

    asyncio.create_task(pump_scanner())
    asyncio.create_task(jup_scanner())
    asyncio.create_task(mempool_runner())
    asyncio.create_task(monitor())

    while True:
        try:
            repair()

            if len(CANDIDATES) < 5:
                await inject_fallback()

            engine.candidate_count = len(CANDIDATES)

            for mint in safe_slice(list(CANDIDATES), 15):
                engine.stats["signals"] += 1

                if not await liquidity_ok(mint):
                    continue

                if not await anti_rug(mint):
                    continue

                a = await alpha(mint)

                if a > 0.01:
                    await buy(mint, a)

            await asyncio.sleep(6)

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"MAIN_ERR {str(e)[:80]}")
            await asyncio.sleep(5)

# ================= ENTRY =================
async def bot_loop():
    try:
        await main()
    except Exception as e:
        log(f"FATAL {e}")
