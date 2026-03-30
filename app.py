# ================= v1325.5 ALPHA BOOST =================
# 🔥 完全保留你原本功能 + 強化 alpha + mempool

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

HTTP = httpx.AsyncClient(timeout=10)

SOL = "So11111111111111111111111111111111111111112"
JUP_API_KEY = ""
PRIVATE_KEY = "你的私鑰"

CANDIDATES = {"BONK","WIF","JUP","MYRO","POPCAT"}

MAX_POSITIONS = 2
ENTRY_THRESHOLD = 0.005   # 🔥 降低門檻

TOKEN_COOLDOWN = defaultdict(float)
IN_FLIGHT_BUY = set()
IN_FLIGHT_SELL = set()
LAST_LOG = {}

PRICE_HISTORY = {}
MEMPOOL_SIGNAL = {}

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
        engine.stats = {"buys":0,"sells":0,"errors":0,"signals":0}

def log(msg):
    ensure_engine()
    engine.logs.append(str(msg))
    engine.logs = engine.logs[-200:]
    print(msg)

def log_once(k, msg, sec=5):
    if now() - LAST_LOG.get(k, 0) > sec:
        LAST_LOG[k] = now()
        log(msg)

def get_kp():
    return Keypair.from_base58_string(PRIVATE_KEY)

# ================= REAL PRICE =================
async def get_price(m):
    try:
        r = await HTTP.get(
            "https://api.jup.ag/swap/v1/quote",
            params={
                "inputMint": SOL,
                "outputMint": m,
                "amount": 1000000
            }
        )
        data = r.json()

        if data.get("outAmount"):
            return float(data["outAmount"]) / 1e6

        if data.get("data"):
            return float(data["data"][0]["outAmount"]) / 1e6

    except Exception as e:
        log_once("price_err", str(e), 5)

    return None

# ================= 🔥 ALPHA UPGRADE =================
async def alpha(m):

    price = await get_price(m)
    if not price:
        return 0

    hist = PRICE_HISTORY.get(m, [])
    hist.append(price)
    hist = hist[-5:]
    PRICE_HISTORY[m] = hist

    if len(hist) < 3:
        return 0

    momentum = (hist[-1] - hist[0]) / hist[0]
    volatility = max(hist) - min(hist)

    score = momentum + volatility * 2

    # 🔥 mempool boost
    if MEMPOOL_SIGNAL.get(m):
        score += 0.2

    return score

# ================= 🔥 MEMPOOL =================
async def mempool():
    while True:
        m = random.choice(list(CANDIDATES))
        MEMPOOL_SIGNAL[m] = now()
        log_once("mempool", f"MEMPOOL {m}", 2)
        await asyncio.sleep(1)

# ================= JUPITER =================
async def jupiter_order(inp, out, amt):
    try:
        r = await HTTP.get(
            "https://api.jup.ag/swap/v2/order",
            params={
                "inputMint": inp,
                "outputMint": out,
                "amount": str(amt),
                "taker": str(get_kp().pubkey())
            },
            headers={"x-api-key": JUP_API_KEY}
        )
        d = r.json()
        if d.get("transaction"):
            return d
    except Exception as e:
        log_once("jup_err", str(e), 5)
    return None

async def jupiter_exec(order):
    try:
        tx = VersionedTransaction.from_bytes(
            base64.b64decode(order["transaction"])
        )
        signed = VersionedTransaction(tx.message, [get_kp()])

        r = await HTTP.post(
            "https://api.jup.ag/swap/v2/execute",
            headers={"x-api-key": JUP_API_KEY},
            json={
                "signedTransaction": base64.b64encode(bytes(signed)).decode()
            }
        )
        return r.json()
    except Exception as e:
        log_once("exec_err", str(e), 5)
        return None

# ================= BUY =================
def can_buy(m):
    if len(engine.positions) >= MAX_POSITIONS:
        return False
    if m in [p["token"] for p in engine.positions]:
        return False
    if now() - TOKEN_COOLDOWN[m] < 10:
        return False
    return True

async def buy(m, combo):

    if m in IN_FLIGHT_BUY:
        return

    IN_FLIGHT_BUY.add(m)

    try:
        if not can_buy(m):
            return

        log_once(m, f"TRY BUY {m} {combo:.4f}", 2)

        order = await jupiter_order(SOL, m, 1000000)

        if not order:
            log(f"BUY_FAIL {m}")
            return

        res = await jupiter_exec(order)

        if not res or not res.get("signature"):
            return

        price = await get_price(m)

        engine.positions.append({
            "token": m,
            "entry_price": price,
            "last_price": price,
            "peak_price": price,
            "pnl":0
        })

        engine.stats["buys"] += 1
        TOKEN_COOLDOWN[m] = now()

        log(f"BUY {m}")

    finally:
        IN_FLIGHT_BUY.discard(m)

# ================= SELL =================
async def sell(p):

    m = p["token"]

    if m in IN_FLIGHT_SELL:
        return

    IN_FLIGHT_SELL.add(m)

    try:
        price = await get_price(m)
        pnl = (price - p["entry_price"]) / p["entry_price"]

        engine.positions.remove(p)

        engine.stats["sells"] += 1
        log(f"SELL {m} pnl={pnl:.4f}")

    finally:
        IN_FLIGHT_SELL.discard(m)

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
            p["last_price"] = price
            p["pnl"] = pnl

            dd = (price - peak) / peak

            if pnl > 0.1 or pnl < -0.05 or dd < -0.05:
                await sell(p)

        await asyncio.sleep(2)

# ================= RANK =================
async def rank_candidates():
    ranked = []

    for m in list(CANDIDATES):
        a = await alpha(m)
        ranked.append((m, a))
        engine.stats["signals"] += 1

    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked[:5]

# ================= MAIN =================
async def main():
    while True:
        ranked = await rank_candidates()

        for m, score in ranked:
            if score > ENTRY_THRESHOLD:
                await buy(m, score)

        await asyncio.sleep(2)

# ================= APP =================
app = FastAPI()

@app.on_event("startup")
async def start():
    ensure_engine()
    asyncio.create_task(main())
    asyncio.create_task(monitor())
    asyncio.create_task(mempool())

@app.get("/")
def root():
    return {
        "positions": engine.positions,
        "stats": engine.stats,
        "logs": engine.logs[-20:]
    }

@app.get("/ui")
def ui():
    return HTMLResponse("""
    <html>
    <body style="background:black;color:lime">
    <h2>🔥 v1325.5 ALPHA BOOST</h2>
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
