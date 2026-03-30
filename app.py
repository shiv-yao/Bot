# ================= v1324 FINAL MERGED =================
# 🔥 保留你全部原始結構，只升級數據與交易

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

# ================= CONFIG =================
SOL = "So11111111111111111111111111111111111111112"
JUP_API_KEY = ""
PRIVATE_KEY = "3vXyAso1UmTkQTkEGFDivSXw9xuB8Zw2ijeDLQvv7MtKDdDRAfod814Bb6NGXZqKd6jtqnNe2rAJc8bCFD6SnWT2"

CANDIDATES = {"BONK","WIF","JUP","MYRO","POPCAT"}

MAX_POSITIONS = 2
ENTRY_THRESHOLD = 0.03

# ================= GLOBAL =================
TOKEN_COOLDOWN = defaultdict(float)
IN_FLIGHT_BUY = set()
IN_FLIGHT_SELL = set()
LAST_LOG = {}
PRICE_CACHE = {}

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

def log_once(key, msg, sec=5):
    if now() - LAST_LOG.get(key, 0) > sec:
        LAST_LOG[key] = now()
        log(msg)

def get_kp():
    return Keypair.from_base58_string(PRIVATE_KEY)

# ================= 🔥 REAL PRICE =================
async def get_price(mint):

    try:
        r = await HTTP.get(
            "https://quote-api.jup.ag/v6/quote",
            params={
                "inputMint": SOL,
                "outputMint": mint,
                "amount": 1000000
            }
        )

        data = r.json()

        if data.get("data"):
            out = float(data["data"][0]["outAmount"])
            return out / 1e6

    except Exception as e:
        log_once("price_err", str(e), 5)

    return None

# ================= 🔥 REAL ALPHA =================
async def alpha(m):

    price = await get_price(m)

    if not price:
        return 0

    old = PRICE_CACHE.get(m)
    PRICE_CACHE[m] = price

    if not old:
        return 0

    return (price - old) / old

# ================= 🔥 JUPITER V2 =================
async def jupiter_order(input_mint, output_mint, amount):

    log_once("jup_call", f"CALL JUP {input_mint[:4]}->{output_mint[:4]}", 2)

    try:
        r = await HTTP.get(
            "https://api.jup.ag/swap/v2/order",
            params={
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": str(amount),
                "slippageBps": 100,
                "taker": str(get_kp().pubkey())
            },
            headers={"x-api-key": JUP_API_KEY}
        )

        data = r.json()

        if data.get("transaction"):
            return data

        log_once("no_tx", "NO TX", 5)

    except Exception as e:
        log_once("jup_err", str(e), 5)

    return None

async def safe_jupiter_execute(order):

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

        log_once(f"try_{m}", f"TRY BUY {m} combo={combo:.4f}", 3)

        order = await jupiter_order(SOL, m, 1000000)

        if not order:
            log_once("buy_fail", f"BUY_FAIL {m}", 5)
            return

        exec_res = await safe_jupiter_execute(order)

        if not exec_res:
            return

        price = await get_price(m)

        engine.positions.append({
            "token": m,
            "entry_price": price,
            "last_price": price,
            "peak_price": price,
            "entry_ts": now(),
            "signature": exec_res.get("signature",""),
            "combo": combo,
            "pnl_pct": 0
        })

        TOKEN_COOLDOWN[m] = now()
        engine.stats["buys"] += 1

        log(f"BUY SUCCESS {m}")

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

        engine.trade_history.append({
            "token": m,
            "pnl_pct": pnl,
            "ts": now()
        })

        engine.stats["sells"] += 1
        log(f"SELL {m} pnl={pnl:.4f}")

    finally:
        IN_FLIGHT_SELL.discard(m)

# ================= MONITOR =================
async def monitor():
    while True:
        try:
            for p in list(engine.positions):
                price = await get_price(p["token"])

                if not price:
                    continue

                pnl = (price - p["entry_price"]) / p["entry_price"]
                peak = max(p["peak_price"], price)

                p["peak_price"] = peak
                p["last_price"] = price
                p["pnl_pct"] = pnl

                drawdown = (price - peak) / peak

                if pnl > 0.1 or pnl < -0.05 or drawdown < -0.05:
                    await sell(p)

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"MONITOR_ERR {e}")

        await asyncio.sleep(2)

# ================= RANK =================
async def rank_candidates():
    ranked = []

    for m in list(CANDIDATES):
        a = await alpha(m)
        combo = a + random.uniform(0.01,0.02)

        ranked.append((m, combo))
        engine.stats["signals"] += 1

    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked[:5]

# ================= MAIN =================
async def main_loop():
    while True:
        try:
            ranked = await rank_candidates()

            log_once("rank", f"RANKED {len(ranked)}", 5)

            for m, combo in ranked:
                if combo > ENTRY_THRESHOLD:
                    await buy(m, combo)

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"ERR {e}")

        await asyncio.sleep(3)

# ================= APP =================
app = FastAPI()

@app.on_event("startup")
async def start():
    ensure_engine()

    engine.positions = []
    engine.trade_history = []
    engine.logs = []
    engine.stats = {"buys":0,"sells":0,"errors":0,"signals":0}

    asyncio.create_task(main_loop())
    asyncio.create_task(monitor())

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
    <h2>🔥 v1324 FINAL</h2>
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
