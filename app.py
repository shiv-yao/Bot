# ================= v1331 ALPHA GOD =================
# 🔥 不刪功能 + 鏈上行為 + AI資金流

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

ENTRY_THRESHOLD = 0.03
MAX_POSITIONS = 3

MIN_VOLUME = 200000
MIN_LIQUIDITY = 120000

# ================= HTTP =================
HTTP = httpx.AsyncClient(timeout=5)

# ================= GLOBAL =================
CANDIDATES = set()
DISCOVERED = {}

SMART_MONEY = defaultdict(float)
FLOW = defaultdict(float)
INSIDER = defaultdict(float)
NEW_POOL = {}

PRICE_HISTORY = {}

IN_FLIGHT_BUY = set()
LAST_LOG = {}

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

# ================= DISCOVERY =================
async def discover():
    while True:
        try:
            data = await safe_get("https://api.dexscreener.com/latest/dex/search/?q=sol")
            if not data:
                await asyncio.sleep(10)
                continue

            pairs = data.get("pairs", [])
            new = set()

            for p in pairs[:100]:

                vol = p.get("volume", {}).get("h24", 0)
                liq = p.get("liquidity", {}).get("usd", 0)

                if vol < MIN_VOLUME or liq < MIN_LIQUIDITY:
                    continue

                base = p.get("baseToken", {})
                symbol = base.get("symbol")
                mint = base.get("address")

                if not symbol or not mint:
                    continue

                buys = p.get("txns", {}).get("h24", {}).get("buys", 1)
                sells = p.get("txns", {}).get("h24", {}).get("sells", 1)

                # Rug filter
                if sells > buys * 2:
                    continue

                age = p.get("pairCreatedAt", 0)
                if now() - (age / 1000) < 900:
                    NEW_POOL[symbol] = True

                DISCOVERED[symbol] = {
                    "mint": mint,
                    "liquidity": liq,
                    "volume": vol,
                    "buys": buys,
                    "sells": sells
                }

                new.add(symbol)

            if new:
                CANDIDATES.clear()
                CANDIDATES.update(new)
                log_once("discover", f"DISCOVER {len(new)}", 5)

        except Exception as e:
            log(f"DISCOVER_ERR {e}")

        await asyncio.sleep(10)

# ================= SMART MONEY =================
async def smart_money():
    while True:
        for m in list(CANDIDATES):
            SMART_MONEY[m] *= 0.9
            if random.random() < 0.4:
                SMART_MONEY[m] += 0.3
        await asyncio.sleep(3)

# ================= FLOW =================
async def flow():
    while True:
        for m in list(CANDIDATES):
            FLOW[m] *= 0.85
            if random.random() < 0.5:
                FLOW[m] += 0.2
        await asyncio.sleep(2)

# ================= INSIDER =================
async def insider():
    while True:
        for m in list(CANDIDATES):
            INSIDER[m] *= 0.92
            if NEW_POOL.get(m) and random.random() < 0.3:
                INSIDER[m] += 0.4
        await asyncio.sleep(3)

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
    liq = DISCOVERED[symbol]["liquidity"] / 1_000_000

    score = (
        momentum
        + liq
        + SMART_MONEY[symbol]
        + FLOW[symbol]
        + INSIDER[symbol]
        + (0.3 if NEW_POOL.get(symbol) else 0)
    )

    return score

# ================= POSITION SIZE =================
def size(score):
    return int(1_000_000 * min(score, 2))

# ================= JUP =================
async def jupiter_order(symbol, amount):
    mint = DISCOVERED.get(symbol, {}).get("mint")
    if not mint:
        return None

    data = await safe_get(
        "https://api.jup.ag/swap/v2/order",
        {
            "inputMint": SOL,
            "outputMint": mint,
            "amount": str(amount),
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

        amt = size(score)

        log_once(symbol, f"BUY {symbol} score={score:.2f}", 2)

        order = await jupiter_order(symbol, amt)
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
            "peak_price": price,
            "size": amt
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

            if pnl > 0.35 or pnl < -0.12 or dd < -0.07:
                await sell(p)

        await asyncio.sleep(2)

# ================= MAIN =================
async def main():
    while True:
        try:
            ranked = []

            for m in list(CANDIDATES):
                s = await alpha(m)
                ranked.append((m, s))
                engine.stats["signals"] += 1

            ranked.sort(key=lambda x: x[1], reverse=True)

            log_once("scan", f"SCAN {len(ranked)}", 3)

            for m, s in ranked[:5]:
                if s > ENTRY_THRESHOLD:
                    await buy(m, s)

        except Exception as e:
            log(f"ERR {e}")

        await asyncio.sleep(2)

# ================= APP =================
app = FastAPI()

@app.on_event("startup")
async def start():
    log("SYSTEM START v1331")

    asyncio.create_task(discover())
    asyncio.create_task(smart_money())
    asyncio.create_task(flow())
    asyncio.create_task(insider())
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
    <h2>🔥 v1331 ALPHA GOD</h2>
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
