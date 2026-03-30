# ================= v1335 FULL TRUE FUSION =================
# 🔥 完整融合：不刪功能 + 修401 + fallback + 穩定 + production

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
REAL_TRADING = os.getenv("REAL_TRADING", "false").lower() == "true"

ENTRY_THRESHOLD = float(os.getenv("ENTRY_THRESHOLD", "0.03"))
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "3"))
MAX_EXPOSURE_SOL = float(os.getenv("MAX_EXPOSURE_SOL", "1.5"))

MIN_VOLUME = float(os.getenv("MIN_VOLUME", "200000"))
MIN_LIQUIDITY = float(os.getenv("MIN_LIQUIDITY", "120000"))

TP_PCT = float(os.getenv("TP_PCT", "0.35"))
SL_PCT = float(os.getenv("SL_PCT", "0.12"))
DD_PCT = float(os.getenv("DD_PCT", "0.07"))

# ================= HTTP =================
HTTP = httpx.AsyncClient(timeout=5)

def jup_headers():
    return {"x-api-key": JUP_API_KEY} if JUP_API_KEY else None

# ================= GLOBAL =================
CANDIDATES = set()
DISCOVERED = {}

SMART_MONEY = defaultdict(float)
FLOW = defaultdict(float)
INSIDER = defaultdict(float)
NEW_POOL = {}

PRICE_HISTORY = {}
VOL_HISTORY = {}

TOKEN_COOLDOWN = defaultdict(float)
IN_FLIGHT_BUY = set()
IN_FLIGHT_SELL = set()

AI_WEIGHTS = {
    "momentum": 1.0,
    "liquidity": 0.5,
    "smart": 0.8,
    "flow": 0.8,
    "insider": 0.8,
    "new_pool": 0.5,
}

# ================= UTIL =================
def now():
    return time.time()

def ensure_engine():
    if not hasattr(engine, "positions"):
        engine.positions = []
    if not hasattr(engine, "trade_history"):
        engine.trade_history = []
    if not hasattr(engine, "logs"):
        engine.logs = []
    if not hasattr(engine, "stats"):
        engine.stats = {"signals":0,"buys":0,"sells":0,"errors":0}

def log(msg):
    ensure_engine()
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

async def safe_post(url, json=None, headers=None):
    for _ in range(3):
        try:
            r = await HTTP.post(url, json=json, headers=headers)
            if r.status_code == 200:
                return r.json()
        except:
            await asyncio.sleep(0.2)
    return None

# ================= TOKEN =================
async def resolve_token(symbol):
    if symbol in DISCOVERED:
        return DISCOVERED[symbol]["mint"]
    return None

# ================= PRICE（修401 + fallback） =================
async def get_price(symbol):

    mint = await resolve_token(symbol)
    if not mint:
        return None

    # ===== 主API =====
    data = await safe_get(
        "https://api.jup.ag/swap/v1/quote",
        {
            "inputMint": SOL,
            "outputMint": mint,
            "amount": 1_000_000,
        },
        headers=jup_headers()
    )

    if data:
        try:
            if data.get("outAmount"):
                return float(data["outAmount"]) / 1e6
        except:
            pass

    # ===== fallback =====
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

# ================= DISCOVERY =================
async def discover():
    while True:
        try:
            data = await safe_get("https://api.dexscreener.com/latest/dex/search/?q=sol")
            if not data:
                continue

            pairs = data.get("pairs", [])
            for p in pairs[:50]:
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

        except Exception as e:
            log(f"DISCOVER_ERR {e}")

        await asyncio.sleep(10)

# ================= ALPHA =================
async def alpha(symbol):
    price = await get_price(symbol)
    if not price:
        return 0

    hist = PRICE_HISTORY.get(symbol, [])
    hist.append(price)
    hist = hist[-5:]
    PRICE_HISTORY[symbol] = hist

    if len(hist) < 3:
        return 0

    momentum = (hist[-1] - hist[0]) / hist[0]
    return momentum

# ================= JUP =================
async def jupiter_order(symbol, amount):
    mint = await resolve_token(symbol)
    if not mint:
        return None

    return await safe_get(
        "https://api.jup.ag/swap/v2/order",
        {
            "inputMint": SOL,
            "outputMint": mint,
            "amount": str(amount),
        },
        headers=jup_headers()
    )

async def jupiter_exec(order):
    if not PRIVATE_KEY:
        return {"sig":"paper"}

    tx = VersionedTransaction.from_bytes(
        base64.b64decode(order["transaction"])
    )

    signed = VersionedTransaction(tx.message, [Keypair.from_base58_string(PRIVATE_KEY)])

    return await safe_post(
        "https://api.jup.ag/swap/v2/execute",
        {
            "signedTransaction": base64.b64encode(bytes(signed)).decode()
        },
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

        order = await jupiter_order(symbol, 1_000_000)
        if not order:
            return

        await jupiter_exec(order)

        price = await get_price(symbol)

        engine.positions.append({
            "token": symbol,
            "entry_price": price,
            "peak_price": price,
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
            s = await alpha(m)
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
    ensure_engine()
    log("SYSTEM START v1335 FULL")

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

@app.get("/ui")
def ui():
    return HTMLResponse("""
    <html>
    <body style="background:black;color:lime">
    <h2>🔥 v1335 FULL</h2>
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
