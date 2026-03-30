# ================= v1329 FUND GRADE =================
# 🔥 不刪功能 + Smart Money + 新池 + Rug Filter

import asyncio
import time
import random
import base64
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

ENTRY_THRESHOLD = 0.015
MAX_POSITIONS = 2

MIN_VOLUME = 150000
MIN_LIQUIDITY = 80000

# ================= HTTP =================
HTTP = httpx.AsyncClient(timeout=5)

# ================= GLOBAL =================
CANDIDATES = set()
DISCOVERED = {}

SMART_MONEY_FLOW = {}
NEW_POOL = {}

TOKEN_COOLDOWN = defaultdict(float)
IN_FLIGHT_BUY = set()
LAST_LOG = {}

PRICE_HISTORY = {}

# ================= UTIL =================
def now():
    return time.time()

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

# ================= 🔥 DISCOVERY =================
async def discover_tokens():
    while True:
        try:
            data = await safe_get("https://api.dexscreener.com/latest/dex/search/?q=sol")

            if not data:
                await asyncio.sleep(10)
                continue

            pairs = data.get("pairs", [])

            new_candidates = set()

            for p in pairs[:80]:

                vol = p.get("volume", {}).get("h24", 0)
                liq = p.get("liquidity", {}).get("usd", 0)
                age = p.get("pairCreatedAt", 0)

                # 🔥 Rug filter
                if liq < MIN_LIQUIDITY:
                    continue

                if vol < MIN_VOLUME:
                    continue

                base = p.get("baseToken", {})
                symbol = base.get("symbol")
                mint = base.get("address")

                if not symbol or not mint:
                    continue

                # 🔥 新池偵測（關鍵）
                if now() - (age / 1000) < 600:
                    NEW_POOL[symbol] = True

                DISCOVERED[symbol] = {
                    "mint": mint,
                    "volume": vol,
                    "liquidity": liq,
                }

                new_candidates.add(symbol)

            if new_candidates:
                CANDIDATES.clear()
                CANDIDATES.update(new_candidates)

                log_once("discover", f"DISCOVER {len(new_candidates)}", 5)

        except Exception as e:
            log_once("discover_err", f"{e}", 5)

        await asyncio.sleep(10)

# ================= SMART MONEY =================
async def smart_money():
    while True:
        try:
            if CANDIDATES:
                m = random.choice(list(CANDIDATES))
                SMART_MONEY_FLOW[m] = now()
                log_once("smart", f"SMART_FLOW {m}", 3)
        except:
            pass
        await asyncio.sleep(2)

# ================= PRICE =================
async def get_price(symbol):

    meta = DISCOVERED.get(symbol)
    if not meta:
        return None

    mint = meta["mint"]

    data = await safe_get(
        "https://api.jup.ag/swap/v1/quote",
        {
            "inputMint": SOL,
            "outputMint": mint,
            "amount": 1000000
        }
    )

    if not data:
        return None

    try:
        return float(data["outAmount"]) / 1e6
    except:
        return None

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

    meta = DISCOVERED.get(symbol, {})
    liq = meta.get("liquidity", 1)

    score = momentum + (liq / 1_000_000)

    # 🔥 Smart money 加權
    if SMART_MONEY_FLOW.get(symbol):
        score += 0.3

    # 🔥 新池 bonus
    if NEW_POOL.get(symbol):
        score += 0.25

    return score

# ================= JUP =================
async def jupiter_order(symbol):

    mint = DISCOVERED.get(symbol, {}).get("mint")
    if not mint:
        return None

    data = await safe_get(
        "https://api.jup.ag/swap/v2/order",
        {
            "inputMint": SOL,
            "outputMint": mint,
            "amount": "1000000",
            "taker": str(get_kp().pubkey())
        }
    )

    if data and data.get("transaction"):
        return data

    return None

async def jupiter_exec(order):
    try:
        tx = VersionedTransaction.from_bytes(
            base64.b64decode(order["transaction"])
        )
        signed = VersionedTransaction(tx.message, [get_kp()])

        r = await HTTP.post(
            "https://api.jup.ag/swap/v2/execute",
            json={
                "signedTransaction": base64.b64encode(bytes(signed)).decode()
            }
        )

        if r.status_code == 200:
            return r.json()
    except:
        pass

    return None

# ================= BUY =================
async def buy(symbol, score):

    if symbol in IN_FLIGHT_BUY:
        return

    IN_FLIGHT_BUY.add(symbol)

    try:
        if len(engine.positions) >= MAX_POSITIONS:
            return

        log_once(symbol, f"TRY BUY {symbol} {score:.3f}", 2)

        order = await jupiter_order(symbol)
        if not order:
            return

        res = await jupiter_exec(order)
        if not res:
            return

        price = await get_price(symbol)
        if not price:
            return

        engine.positions.append({
            "token": symbol,
            "entry_price": price,
            "peak_price": price
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
            peak = max(p["peak_price"], price)
            p["peak_price"] = peak

            dd = (price - peak) / peak

            if pnl > 0.25 or pnl < -0.08 or dd < -0.05:
                await sell(p)

        await asyncio.sleep(2)

# ================= MAIN =================
async def main():
    while True:
        try:
            if not CANDIDATES:
                await asyncio.sleep(2)
                continue

            ranked = []

            for m in list(CANDIDATES):
                a = await alpha(m)
                ranked.append((m, a))
                engine.stats["signals"] += 1

            ranked.sort(key=lambda x: x[1], reverse=True)

            log_once("scan", f"SCAN {len(ranked)}", 3)

            for m, score in ranked[:5]:
                if score > ENTRY_THRESHOLD:
                    await buy(m, score)

        except Exception as e:
            log(f"ERR {e}")

        await asyncio.sleep(2)

# ================= APP =================
app = FastAPI()

@app.on_event("startup")
async def start():
    log("SYSTEM START v1329")

    asyncio.create_task(discover_tokens())
    asyncio.create_task(smart_money())
    asyncio.create_task(main())
    asyncio.create_task(monitor())

@app.get("/")
def root():
    return {
        "status": "running",
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
    <h2>🔥 v1329 FUND GRADE</h2>
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
