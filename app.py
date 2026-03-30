# ================= v1338 FULL TRUE FUSION =================
# 🔥 不刪功能 + AI + fallback + 自動調參 + production

import os
import asyncio
import time
import random
import base64
import traceback
from collections import defaultdict

import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

from state import engine

# ================= CONFIG =================
SOL = "So11111111111111111111111111111111111111112"

PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")
JUP_API_KEY = os.getenv("JUP_API_KEY", "")

ENTRY_THRESHOLD = float(os.getenv("ENTRY_THRESHOLD", 0.03))
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", 3))

MIN_VOLUME = float(os.getenv("MIN_VOLUME", 100000))
MIN_LIQUIDITY = float(os.getenv("MIN_LIQUIDITY", 50000))

TP_PCT = float(os.getenv("TP_PCT", 0.35))
SL_PCT = float(os.getenv("SL_PCT", 0.12))
DD_PCT = float(os.getenv("DD_PCT", 0.07))

# ================= HTTP =================
HTTP = httpx.AsyncClient(timeout=5)

def headers():
    return {"x-api-key": JUP_API_KEY} if JUP_API_KEY else None

# ================= GLOBAL =================
CANDIDATES = set()
DISCOVERED = {}

PRICE_HISTORY = {}
VOL_HISTORY = {}

SMART_MONEY = defaultdict(float)
FLOW = defaultdict(float)
INSIDER = defaultdict(float)

TOKEN_CACHE = {}
TOKEN_TS = {}

AI_WEIGHTS = {
    "momentum": 1.0,
    "liquidity": 0.5,
    "smart": 0.8,
    "flow": 0.8,
    "insider": 0.8,
}

# ================= UTIL =================
def now():
    return time.time()

def log(msg):
    engine.logs.append(str(msg))
    engine.logs = engine.logs[-200:]
    print(msg, flush=True)

# ================= SAFE HTTP =================
async def safe_get(url, params=None, headers=None):
    for _ in range(3):
        try:
            r = await HTTP.get(url, params=params, headers=headers)
            if r.status_code == 200:
                return r.json()
        except:
            await asyncio.sleep(0.2)
    return None

# ================= DISCOVERY =================
async def discover():
    while True:
        try:
            data = await safe_get("https://api.dexscreener.com/latest/dex/search/?q=sol")

            if not data:
                log("WAIT TOKENS...")
                await asyncio.sleep(5)
                continue

            pairs = data.get("pairs", [])
            found = 0

            for p in pairs[:100]:

                symbol = p["baseToken"]["symbol"]
                mint = p["baseToken"]["address"]

                vol = p.get("volume", {}).get("h24", 0)
                liq = p.get("liquidity", {}).get("usd", 0)

                if vol < MIN_VOLUME or liq < MIN_LIQUIDITY:
                    continue

                DISCOVERED[symbol] = {
                    "mint": mint,
                    "volume": vol,
                    "liquidity": liq,
                }

                CANDIDATES.add(symbol)
                found += 1

            if found == 0:
                log("⚠️ NO TOKENS")
            else:
                log(f"🔥 DISCOVER OK: {found} tokens")

        except Exception as e:
            log(f"DISCOVER_ERR {e}")

        await asyncio.sleep(10)

# ================= PRICE =================
async def get_price(symbol):

    meta = DISCOVERED.get(symbol)
    if not meta:
        return None

    mint = meta["mint"]

    # main
    data = await safe_get(
        "https://api.jup.ag/swap/v1/quote",
        {
            "inputMint": SOL,
            "outputMint": mint,
            "amount": 1_000_000,
        },
        headers=headers()
    )

    if data:
        try:
            return float(data["outAmount"]) / 1e6
        except:
            pass

    # fallback
    data = await safe_get(
        "https://quote-api.jup.ag/v6/quote",
        {
            "inputMint": SOL,
            "outputMint": mint,
            "amount": 1_000_000,
        }
    )

    if data and data.get("data"):
        return float(data["data"][0]["outAmount"]) / 1e6

    return None

# ================= FEATURES =================
async def features(symbol):

    price = await get_price(symbol)
    if not price:
        return None

    hist = PRICE_HISTORY.get(symbol, [])
    hist.append(price)
    hist = hist[-5:]
    PRICE_HISTORY[symbol] = hist

    if len(hist) < 3:
        return None

    momentum = (hist[-1] - hist[0]) / hist[0]
    liquidity = DISCOVERED[symbol]["liquidity"] / 1_000_000

    return {
        "momentum": momentum,
        "liquidity": liquidity,
        "smart": SMART_MONEY[symbol],
        "flow": FLOW[symbol],
        "insider": INSIDER[symbol],
    }

# ================= AI =================
def score(f):
    return sum(f[k] * AI_WEIGHTS[k] for k in f)

def learn(f, pnl):
    for k in AI_WEIGHTS:
        AI_WEIGHTS[k] += 0.02 * pnl * f.get(k, 0)
        AI_WEIGHTS[k] = max(min(AI_WEIGHTS[k], 2), -1)

# ================= ALPHA =================
async def alpha(symbol):
    f = await features(symbol)
    if not f:
        return 0, None
    return score(f), f

# ================= BUY =================
async def buy(symbol, score_val):

    if len(engine.positions) >= MAX_POSITIONS:
        return

    f = await features(symbol)
    if not f:
        return

    price = await get_price(symbol)
    if not price:
        return

    engine.positions.append({
        "token": symbol,
        "entry_price": price,
        "peak": price,
        "features": f
    })

    engine.stats["buys"] += 1
    log(f"BUY {symbol}")

# ================= SELL =================
async def sell(p):

    price = await get_price(p["token"])
    if not price:
        return

    pnl = (price - p["entry_price"]) / p["entry_price"]

    learn(p["features"], pnl)

    engine.positions.remove(p)
    engine.stats["sells"] += 1

    log(f"SELL {p['token']} pnl={pnl:.3f}")

# ================= MONITOR =================
async def monitor():
    while True:

        for p in list(engine.positions):

            price = await get_price(p["token"])
            if not price:
                continue

            pnl = (price - p["entry_price"]) / p["entry_price"]

            if pnl > TP_PCT or pnl < -SL_PCT:
                await sell(p)

        await asyncio.sleep(2)

# ================= MAIN =================
async def main():
    while True:

        ranked = []

        for m in list(CANDIDATES):
            s, _ = await alpha(m)
            ranked.append((m, s))
            engine.stats["signals"] += 1

        ranked.sort(key=lambda x: x[1], reverse=True)

        for m, s in ranked[:5]:
            if s > ENTRY_THRESHOLD:
                await buy(m, s)

        await asyncio.sleep(2)

# ================= APP =================
app = FastAPI()

@app.on_event("startup")
async def start():
    log("🔥 SYSTEM START v1338")

    asyncio.create_task(discover())
    asyncio.create_task(main())
    asyncio.create_task(monitor())

@app.get("/")
def root():
    return {
        "positions": engine.positions,
        "stats": engine.stats,
        "candidates": list(CANDIDATES),
        "logs": engine.logs[-20:]
    }
