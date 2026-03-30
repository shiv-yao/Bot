# ================= v1336.5 FULL TRUE FUSION =================
# 🔥 完整融合版（不刪功能 + AI + 爆量 + 穩定）

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
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "").strip()
JUP_API_KEY = os.getenv("JUP_API_KEY", "").strip()

ENTRY_THRESHOLD = float(os.getenv("ENTRY_THRESHOLD", "0.03"))
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "3"))

MIN_VOLUME = float(os.getenv("MIN_VOLUME", "200000"))
MIN_LIQUIDITY = float(os.getenv("MIN_LIQUIDITY", "120000"))

TP_PCT = float(os.getenv("TP_PCT", "0.35"))
SL_PCT = float(os.getenv("SL_PCT", "0.12"))

HTTP = httpx.AsyncClient(timeout=5)

# ================= GLOBAL =================
CANDIDATES = set()
DISCOVERED = {}

SMART_MONEY = defaultdict(float)
FLOW = defaultdict(float)
INSIDER = defaultdict(float)
NEW_POOL = {}

PRICE_HISTORY = {}
VOL_HISTORY = {}

VOLUME_SPIKE = defaultdict(float)
LAST_VOLUME = {}

TOKEN_CACHE = {}
TOKEN_TS = {}
TOKEN_TTL = 1800

IN_FLIGHT_BUY = set()
IN_FLIGHT_SELL = set()

LAST_LOG = {}
ERROR_COUNT = 0

AI_WEIGHTS = {
    "momentum": 1.0,
    "liquidity": 0.5,
    "smart": 0.8,
    "flow": 0.8,
    "insider": 0.8,
    "new_pool": 0.5,
}
LEARNING_RATE = 0.03

# ================= UTIL =================
def now():
    return time.time()

def ensure_engine():
    if not hasattr(engine, "positions"):
        engine.positions = []
    if not hasattr(engine, "logs"):
        engine.logs = []
    if not hasattr(engine, "stats"):
        engine.stats = {"signals":0,"buys":0,"sells":0,"errors":0}

def log(msg):
    ensure_engine()
    engine.logs.append(str(msg))
    engine.logs = engine.logs[-200:]
    print(msg, flush=True)

def log_once(k, msg, sec=5):
    if now() - LAST_LOG.get(k, 0) > sec:
        LAST_LOG[k] = now()
        log(msg)

def jup_headers():
    return {"x-api-key": JUP_API_KEY} if JUP_API_KEY else None

def get_kp():
    return Keypair.from_base58_string(PRIVATE_KEY)

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

async def safe_post(url, json=None, headers=None):
    for _ in range(3):
        try:
            r = await HTTP.post(url, json=json, headers=headers)
            if r.status_code == 200:
                return r.json()
        except:
            await asyncio.sleep(0.2)
    return None

# ================= TOKEN RESOLVE =================
async def resolve_token(symbol):

    if symbol in DISCOVERED:
        return DISCOVERED[symbol]["mint"]

    cached = TOKEN_CACHE.get(symbol)
    if cached and now() - TOKEN_TS.get(symbol, 0) < TOKEN_TTL:
        return cached

    data = await safe_get("https://token.jup.ag/all")
    if data:
        for t in data:
            if t.get("symbol","").upper() == symbol:
                mint = t.get("address")
                TOKEN_CACHE[symbol] = mint
                TOKEN_TS[symbol] = now()
                return mint

    return None

# ================= PRICE（修401） =================
async def get_price(symbol):

    mint = await resolve_token(symbol)
    if not mint:
        return None

    data = await safe_get(
        "https://api.jup.ag/swap/v1/quote",
        {"inputMint": SOL, "outputMint": mint, "amount": 1_000_000},
        headers=jup_headers()
    )

    if data:
        try:
            return float(data["outAmount"]) / 1e6
        except:
            pass

    data = await safe_get(
        "https://quote-api.jup.ag/v6/quote",
        {"inputMint": SOL, "outputMint": mint, "amount": 1_000_000}
    )

    if data and data.get("data"):
        return float(data["data"][0]["outAmount"]) / 1e6

    return None

# ================= DISCOVERY =================
async def discover():
    while True:
        try:
            data = await safe_get("https://api.dexscreener.com/latest/dex/pairs/solana")
            if not data:
                continue

            new = set()

            for p in data.get("pairs", [])[:80]:

                symbol = (p["baseToken"]["symbol"] or "").upper()
                mint = p["baseToken"]["address"]

                if symbol in ["SOL","USDC","USDT"]:
                    continue

                vol = float(p.get("volume",{}).get("h24",0))
                liq = float(p.get("liquidity",{}).get("usd",0))

                if vol < MIN_VOLUME or liq < MIN_LIQUIDITY:
                    continue

                prev = LAST_VOLUME.get(symbol, vol)
                spike = (vol - prev) / max(prev,1)

                if spike > 0.5:
                    VOLUME_SPIKE[symbol] += spike

                LAST_VOLUME[symbol] = vol

                age = p.get("pairCreatedAt",0)
                if age and now() - age/1000 < 600:
                    NEW_POOL[symbol] = True

                DISCOVERED[symbol] = {
                    "mint": mint,
                    "volume": vol,
                    "liquidity": liq
                }

                new.add(symbol)

            if new:
                CANDIDATES.clear()
                CANDIDATES.update(new)
                log(f"DISCOVER {len(new)}")

        except Exception as e:
            log(f"DISCOVER_ERR {e}")

        await asyncio.sleep(10)

# ================= FEATURE =================
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

    momentum = (hist[-1]-hist[0])/hist[0]

    return {
        "momentum": momentum,
        "liquidity": DISCOVERED[symbol]["liquidity"]/1_000_000,
        "smart": SMART_MONEY[symbol],
        "flow": FLOW[symbol],
        "insider": INSIDER[symbol],
        "new_pool": 1 if NEW_POOL.get(symbol) else 0
    }

# ================= AI =================
def ai_score(f):
    return sum(f[k]*AI_WEIGHTS[k] for k in f)

def learn(f, pnl):
    for k in AI_WEIGHTS:
        AI_WEIGHTS[k] += LEARNING_RATE * pnl * f.get(k,0)

# ================= ALPHA =================
async def alpha(symbol):

    f = await features(symbol)
    if not f:
        return 0, None

    score = ai_score(f)

    # 🔥 v1336 加強
    score += VOLUME_SPIKE.get(symbol,0)
    if NEW_POOL.get(symbol):
        score += 0.3

    return score, f

# ================= JUP =================
async def jupiter_order(symbol):
    mint = await resolve_token(symbol)
    if not mint:
        return None

    return await safe_get(
        "https://api.jup.ag/swap/v2/order",
        {"inputMint": SOL,"outputMint": mint,"amount":"1000000"},
        headers=jup_headers()
    )

async def jupiter_exec(order):
    if not PRIVATE_KEY:
        return {"paper":True}

    tx = VersionedTransaction.from_bytes(base64.b64decode(order["transaction"]))
    signed = VersionedTransaction(tx.message, [get_kp()])

    return await safe_post(
        "https://api.jup.ag/swap/v2/execute",
        {"signedTransaction": base64.b64encode(bytes(signed)).decode()},
        headers=jup_headers()
    )

# ================= BUY =================
async def buy(symbol, score):

    if symbol in IN_FLIGHT_BUY:
        return

    IN_FLIGHT_BUY.add(symbol)

    try:
        if len(engine.positions) >= MAX_POSITIONS:
            return

        # v1336 過濾
        if VOLUME_SPIKE.get(symbol,0) < 0.1 and not NEW_POOL.get(symbol):
            return

        order = await jupiter_order(symbol)
        if not order:
            return

        await jupiter_exec(order)

        price = await get_price(symbol)

        engine.positions.append({
            "token":symbol,
            "entry_price":price,
            "peak_price":price,
            "features":{}
        })

        engine.stats["buys"] += 1
        log(f"BUY {symbol}")

    finally:
        IN_FLIGHT_BUY.discard(symbol)

# ================= SELL =================
async def sell(p):

    price = await get_price(p["token"])
    if not price:
        return

    pnl = (price - p["entry_price"]) / p["entry_price"]

    learn(p.get("features",{}), pnl)

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
            s,_ = await alpha(m)
            ranked.append((m,s))
            engine.stats["signals"] += 1

        ranked.sort(key=lambda x:x[1], reverse=True)

        for m,s in ranked[:5]:
            if s > ENTRY_THRESHOLD:
                await buy(m,s)

        await asyncio.sleep(2)

# ================= SAFE =================
async def safe_task(name, coro):
    global ERROR_COUNT
    while True:
        try:
            await coro()
        except Exception as e:
            ERROR_COUNT += 1
            log(f"[CRASH] {name} {e}")
            traceback.print_exc()
            await asyncio.sleep(2)

# ================= APP =================
app = FastAPI()

@app.on_event("startup")
async def start():
    ensure_engine()
    log("SYSTEM START v1336.5 FULL")

    asyncio.create_task(safe_task("discover", discover))
    asyncio.create_task(safe_task("main", main))
    asyncio.create_task(safe_task("monitor", monitor))

@app.get("/")
def root():
    return {
        "positions": engine.positions,
        "stats": engine.stats,
        "candidates": list(CANDIDATES),
        "logs": engine.logs[-20:]
    }

@app.get("/ui")
def ui():
    return HTMLResponse("""
    <html>
    <body style="background:black;color:lime">
    <h2>🔥 v1336.5 FULL</h2>
    <div id=data></div>
    <script>
    async function load(){
        let r = await fetch('/');
        let d = await r.json();
        document.getElementById('data').innerHTML =
        '<pre>'+JSON.stringify(d,null,2)+'</pre>';
    }
    setInterval(load,2000);
    load();
    </script>
    </body>
    </html>
    """)
