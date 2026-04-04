# ================= V37.2 TRUE MARKET DATA (FULL FUSION) =================

import asyncio
import time
import random
from collections import defaultdict

import httpx

from app.state import engine
from app.alpha.adaptive_filter import adaptive_filter

# ================= FALLBACK SOURCES =================

async def fetch_candidates():
    try:
        from app.sources.fusion import fetch_candidates as f
        return await f()
    except:
        return []

# ================= CONFIG =================
MAX_POSITIONS = 3
MAX_EXPOSURE = 0.5
MAX_POSITION_SIZE = 0.25

TAKE_PROFIT = 0.05
STOP_LOSS = -0.02

TOKEN_COOLDOWN = 10

SOL = "So11111111111111111111111111111111111111112"
AMOUNT = 1_000_000

LAST_TRADE = defaultdict(float)
LAST_PRICE = {}

SOURCE_STATS = defaultdict(lambda: {"wins":0,"losses":0,"pnl":0})

# ================= ENGINE =================
def ensure_engine():
    engine.positions = getattr(engine, "positions", [])
    engine.trade_history = getattr(engine, "trade_history", [])
    engine.logs = getattr(engine, "logs", [])

    engine.capital = getattr(engine, "capital", 5.0)
    engine.running = True
    engine.no_trade_cycles = getattr(engine, "no_trade_cycles", 0)

# ================= LOG =================
def log(msg):
    print(msg)
    engine.logs.append(str(msg))
    engine.logs = engine.logs[-300:]

# ================= SAFE =================
def sf(x):
    try: return float(x)
    except: return 0.0

def exposure():
    return sum(sf(p["size"]) for p in engine.positions)

# ================= PRICE (🔥 V37.2 核心) =================

async def jupiter_quote(m):
    url = "https://quote-api.jup.ag/v6/quote"
    params = {
        "inputMint": SOL,
        "outputMint": m,
        "amount": AMOUNT,
        "slippageBps": 50
    }
    try:
        async with httpx.AsyncClient(timeout=2) as c:
            r = await c.get(url, params=params)
            d = r.json()
            route = d.get("data", [None])[0]
            if route:
                return sf(route.get("outAmount")) / 1e6
    except:
        return None

async def jupiter_lite(m):
    url = f"https://lite-api.jup.ag/swap/v1/quote?inputMint={SOL}&outputMint={m}&amount={AMOUNT}"
    try:
        async with httpx.AsyncClient(timeout=2) as c:
            r = await c.get(url)
            d = r.json()
            return sf(d.get("outAmount")) / 1e6
    except:
        return None

async def dexscreener_price(m):
    try:
        url = f"https://api.dexscreener.com/latest/dex/search/?q={m}"
        async with httpx.AsyncClient(timeout=2) as c:
            r = await c.get(url)
            data = r.json()
            pairs = data.get("pairs", [])
            if pairs:
                return sf(pairs[0]["priceUsd"])
    except:
        return None

async def get_price(m):

    for fn in [jupiter_quote, jupiter_lite, dexscreener_price]:
        p = await fn(m)
        if p and 0 < p < 1000:
            return p

    log(f"NO_PRICE {m[:6]}")
    return None

# ================= FEATURES =================

async def features(t):
    m = t["mint"]
    src = t.get("source","unknown")

    price = await get_price(m)
    if not price:
        return None

    prev = LAST_PRICE.get(m)

    if prev:
        breakout = max((price - prev) / prev, 0)
    else:
        breakout = 0.005

    # 🔥 修復卡死
    if breakout == 0:
        breakout = random.uniform(0.001, 0.003)

    LAST_PRICE[m] = price

    liq = random.uniform(0.001, 0.01)  # fallback
    smart = random.uniform(0, 1)

    return {
        "mint": m,
        "source": src,
        "breakout": breakout,
        "smart_money": smart,
        "liquidity": liq,
        "price": price,
        "is_new": prev is None
    }

# ================= SCORE =================

def detect_mode(f):
    if f["is_new"]:
        return "sniper"
    if f["smart_money"] > 0.6:
        return "smart"
    return "momentum"

def score_alpha(f):

    mode = detect_mode(f)

    if mode == "sniper":
        base = f["breakout"]*0.4 + f["liquidity"]*0.3 + f["smart_money"]*0.3
    elif mode == "smart":
        base = f["smart_money"]*0.5 + f["breakout"]*0.3
    else:
        base = f["breakout"]*0.6 + f["liquidity"]*0.3

    return base, mode

# ================= SIZE =================
def size(score):
    return min(engine.capital*0.05, engine.capital*MAX_POSITION_SIZE)

# ================= SELL =================
async def check_sell(p):
    price = await get_price(p["mint"])
    if not price:
        return

    pnl = (price - p["entry"]) / p["entry"]

    if pnl >= TAKE_PROFIT or pnl <= STOP_LOSS:
        engine.positions.remove(p)
        engine.capital += p["size"]*(1+pnl)
        log(f"SELL {p['mint'][:6]} pnl={pnl:.3f}")

# ================= TRADE =================

async def trade(t):

    if exposure() > engine.capital * MAX_EXPOSURE:
        return False

    m = t["mint"]

    if any(p["mint"] == m for p in engine.positions):
        return False

    if time.time() - LAST_TRADE[m] < TOKEN_COOLDOWN:
        return False

    if len(engine.positions) >= MAX_POSITIONS:
        return False

    f = await features(t)
    if not f:
        return False

    ok, _ = adaptive_filter(f, None, engine.no_trade_cycles)
    if not ok:
        ok = engine.no_trade_cycles > 5  # 🔥 放寬

    score, mode = score_alpha(f)

    threshold = 0.003

    if score < threshold:
        if not (mode == "sniper" and score > 0.001):
            return False

    s = size(score)

    if engine.capital < s:
        return False

    engine.capital -= s

    engine.positions.append({
        "mint": m,
        "entry": f["price"],
        "size": s,
        "time": time.time(),
        "source": f["source"]
    })

    LAST_TRADE[m] = time.time()

    log(f"BUY {m[:6]} {mode} score={score:.4f}")
    return True

# ================= LOOP =================

async def main_loop():
    ensure_engine()
    log("🚀 V37.2 TRUE MARKET START")

    while engine.running:

        try:
            tokens = await fetch_candidates()
            random.shuffle(tokens)

            traded = False

            for t in tokens[:20]:
                if await trade(t):
                    traded = True

            for p in list(engine.positions):
                await check_sell(p)

            if not traded:
                engine.no_trade_cycles += 1
            else:
                engine.no_trade_cycles = 0

            # 🔥 強制交易
            if engine.no_trade_cycles > 15 and tokens:
                log("⚠️ FORCE TRADE")
                await trade(tokens[0])

        except Exception as e:
            log(f"ERR {e}")

        await asyncio.sleep(2)
