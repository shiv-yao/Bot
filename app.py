# ================= v1332 REAL AI =================
# 🔥 不刪功能 + AI學習 + 自動調參

import asyncio, time, random, base64
from collections import defaultdict

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from state import engine
import httpx
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

# ================= CONFIG =================
SOL = "So11111111111111111111111111111111111111112"
PRIVATE_KEY = "換你的私鑰"

ENTRY_THRESHOLD = 0.05
MAX_POSITIONS = 3

MIN_VOLUME = 200000
MIN_LIQUIDITY = 120000

HTTP = httpx.AsyncClient(timeout=5)

# ================= GLOBAL =================
CANDIDATES = set()
DISCOVERED = {}

SMART_MONEY = defaultdict(float)
FLOW = defaultdict(float)
INSIDER = defaultdict(float)
NEW_POOL = {}

PRICE_HISTORY = {}

# 🔥 AI 權重（核心）
AI_WEIGHTS = {
    "momentum": 1.0,
    "liquidity": 0.5,
    "flow": 0.5,
    "smart": 0.8,
    "insider": 0.8,
    "new": 0.5
}

LEARNING_RATE = 0.05

IN_FLIGHT_BUY = set()
LAST_LOG = {}

# ================= UTIL =================
def now(): return time.time()

def log(msg):
    engine.logs.append(str(msg))
    engine.logs = engine.logs[-200:]
    print(msg, flush=True)

def log_once(k, msg, sec=5):
    if now() - LAST_LOG.get(k, 0) > sec:
        LAST_LOG[k] = now()
        log(msg)

def get_kp():
    return Keypair.from_base58_string(PRIVATE_KEY)

# ================= SAFE HTTP =================
async def safe_get(url, params=None):
    for _ in range(3):
        try:
            r = await HTTP.get(url, params=params)
            if r.status_code == 200:
                return r.json()
        except:
            await asyncio.sleep(0.2)
    return None

# ================= DISCOVERY =================
async def discover():
    while True:
        data = await safe_get("https://api.dexscreener.com/latest/dex/search/?q=sol")
        if not data:
            await asyncio.sleep(10)
            continue

        pairs = data.get("pairs", [])
        new = set()

        for p in pairs[:80]:
            vol = p.get("volume", {}).get("h24", 0)
            liq = p.get("liquidity", {}).get("usd", 0)

            if vol < MIN_VOLUME or liq < MIN_LIQUIDITY:
                continue

            symbol = p.get("baseToken", {}).get("symbol")
            mint = p.get("baseToken", {}).get("address")

            if not symbol or not mint:
                continue

            DISCOVERED[symbol] = {
                "mint": mint,
                "liquidity": liq
            }

            new.add(symbol)

        if new:
            CANDIDATES.clear()
            CANDIDATES.update(new)
            log_once("discover", f"DISCOVER {len(new)}", 5)

        await asyncio.sleep(10)

# ================= PRICE =================
async def get_price(symbol):
    meta = DISCOVERED.get(symbol)
    if not meta:
        return None

    data = await safe_get(
        "https://api.jup.ag/swap/v1/quote",
        {
            "inputMint": SOL,
            "outputMint": meta["mint"],
            "amount": 1000000
        }
    )

    if not data:
        return None

    try:
        return float(data["outAmount"]) / 1e6
    except:
        return None

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

    momentum = (hist[-1] - hist[0]) / hist[0]
    liq = DISCOVERED[symbol]["liquidity"] / 1_000_000

    return {
        "momentum": momentum,
        "liquidity": liq,
        "flow": FLOW[symbol],
        "smart": SMART_MONEY[symbol],
        "insider": INSIDER[symbol],
        "new": 1 if NEW_POOL.get(symbol) else 0
    }

# ================= AI SCORE =================
def ai_score(f):
    return sum(f[k] * AI_WEIGHTS[k] for k in f)

# ================= LEARNING =================
def learn(f, pnl):
    for k in AI_WEIGHTS:
        AI_WEIGHTS[k] += LEARNING_RATE * pnl * f[k]

# ================= BUY =================
async def buy(symbol, score, f):

    if symbol in IN_FLIGHT_BUY:
        return

    IN_FLIGHT_BUY.add(symbol)

    try:
        if len(engine.positions) >= MAX_POSITIONS:
            return

        order = await safe_get(
            "https://api.jup.ag/swap/v2/order",
            {
                "inputMint": SOL,
                "outputMint": DISCOVERED[symbol]["mint"],
                "amount": "1000000",
                "taker": str(get_kp().pubkey())
            }
        )

        if not order:
            return

        price = await get_price(symbol)
        if not price:
            return

        engine.positions.append({
            "token": symbol,
            "entry_price": price,
            "features": f
        })

        engine.stats["buys"] += 1

    finally:
        IN_FLIGHT_BUY.discard(symbol)

# ================= SELL =================
async def sell(p):

    price = await get_price(p["token"])
    if not price:
        return

    pnl = (price - p["entry_price"]) / p["entry_price"]

    # 🔥 AI 學習
    learn(p["features"], pnl)

    engine.positions.remove(p)
    engine.stats["sells"] += 1

    log(f"SELL {p['token']} pnl={pnl:.3f} weights={AI_WEIGHTS}")

# ================= MONITOR =================
async def monitor():
    while True:
        for p in list(engine.positions):
            price = await get_price(p["token"])
            if not price:
                continue

            pnl = (price - p["entry_price"]) / p["entry_price"]

            if pnl > 0.4 or pnl < -0.15:
                await sell(p)

        await asyncio.sleep(2)

# ================= MAIN =================
async def main():
    while True:
        ranked = []

        for m in list(CANDIDATES):
            f = await features(m)
            if not f:
                continue

            s = ai_score(f)
            ranked.append((m, s, f))
            engine.stats["signals"] += 1

        ranked.sort(key=lambda x: x[1], reverse=True)

        for m, s, f in ranked[:5]:
            if s > ENTRY_THRESHOLD:
                await buy(m, s, f)

        await asyncio.sleep(2)

# ================= APP =================
app = FastAPI()

@app.on_event("startup")
async def start():
    log("SYSTEM START v1332 AI")

    asyncio.create_task(discover())
    asyncio.create_task(main())
    asyncio.create_task(monitor())

@app.get("/")
def root():
    return {
        "positions": engine.positions,
        "stats": engine.stats,
        "weights": AI_WEIGHTS,
        "logs": engine.logs[-20:]
    }

@app.get("/ui")
def ui():
    return HTMLResponse("""
    <html><body style="background:black;color:lime">
    <h2>🔥 v1332 REAL AI</h2>
    <div id=data></div>
    <script>
    async function load(){
        let r=await fetch('/');
        let d=await r.json();
        document.getElementById('data').innerHTML =
        '<pre>'+JSON.stringify(d,null,2)+'</pre>';
    }
    setInterval(load,2000);
    load();
    </script>
    </body></html>
    """)
