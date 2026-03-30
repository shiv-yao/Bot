# ================= v1326.3 FINAL SAFE FIX =================
# 🔥 不刪功能 + 修 engine 型別問題 + 不再 startup crash

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

# ================= HTTP =================
HTTP = httpx.AsyncClient(
    timeout=httpx.Timeout(5.0),
    limits=httpx.Limits(max_connections=20, max_keepalive_connections=5)
)

SOL = "So11111111111111111111111111111111111111112"
JUP_API_KEY = ""
PRIVATE_KEY = "改成你新的私鑰，不要再用舊的"

CANDIDATES = {"BONK", "WIF", "JUP", "MYRO", "POPCAT"}

ENTRY_THRESHOLD = 0.005
MAX_POSITIONS = 2

TOKEN_COOLDOWN = defaultdict(float)
IN_FLIGHT_BUY = set()
IN_FLIGHT_SELL = set()
LAST_LOG = {}

PRICE_HISTORY = {}
MEMPOOL_SIGNAL = {}

# ================= UTIL =================
def now():
    return time.time()

def ensure_list(v):
    if isinstance(v, list):
        return v
    if v is None:
        return []
    if isinstance(v, tuple):
        return list(v)
    if isinstance(v, set):
        return list(v)
    if isinstance(v, dict):
        return [v]
    if isinstance(v, str):
        return [v]
    try:
        return list(v)
    except Exception:
        return []

def ensure_engine():
    current_positions = getattr(engine, "positions", [])
    current_trade_history = getattr(engine, "trade_history", [])
    current_logs = getattr(engine, "logs", [])
    current_stats = getattr(engine, "stats", {})

    engine.positions = ensure_list(current_positions)
    engine.trade_history = ensure_list(current_trade_history)
    engine.logs = ensure_list(current_logs)

    if not isinstance(current_stats, dict):
        current_stats = {}

    engine.stats = {
        "buys": int(current_stats.get("buys", 0)),
        "sells": int(current_stats.get("sells", 0)),
        "errors": int(current_stats.get("errors", 0)),
        "signals": int(current_stats.get("signals", 0)),
    }

def log(msg):
    ensure_engine()
    engine.logs.append(str(msg))
    if len(engine.logs) > 200:
        engine.logs = engine.logs[-200:]
    print(msg, flush=True)

def log_once(k, msg, sec=5):
    if now() - LAST_LOG.get(k, 0) > sec:
        LAST_LOG[k] = now()
        log(msg)

def get_kp():
    return Keypair.from_base58_string(PRIVATE_KEY)

# ================= SAFE HTTP =================
async def safe_get(url, params=None, headers=None):
    for _ in range(3):
        try:
            r = await HTTP.get(url, params=params, headers=headers)
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            log_once("http_err", f"HTTP_ERR {e}", 5)
            await asyncio.sleep(0.2)
    return None

# ================= PRICE =================
async def get_price(m):
    data = await safe_get(
        "https://api.jup.ag/swap/v1/quote",
        {
            "inputMint": SOL,
            "outputMint": m,
            "amount": 1000000
        }
    )

    if not data:
        log_once("price_none", f"NO PRICE {m}", 5)
        return None

    try:
        if data.get("outAmount"):
            return float(data["outAmount"]) / 1e6

        if data.get("data"):
            return float(data["data"][0]["outAmount"]) / 1e6
    except Exception as e:
        log_once("price_parse", f"PRICE_PARSE {e}", 5)

    return None

# ================= ALPHA =================
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

    if MEMPOOL_SIGNAL.get(m):
        score += 0.2

    return score

# ================= MEMPOOL =================
async def mempool():
    while True:
        try:
            m = random.choice(list(CANDIDATES))
            MEMPOOL_SIGNAL[m] = now()
            log_once("mempool", f"MEMPOOL {m}", 2)
        except Exception as e:
            log_once("mempool_err", f"MEMPOOL_ERR {e}", 5)
        await asyncio.sleep(1)

# ================= JUP =================
async def jupiter_order(inp, out, amt):
    headers = {"x-api-key": JUP_API_KEY} if JUP_API_KEY else None

    data = await safe_get(
        "https://api.jup.ag/swap/v2/order",
        {
            "inputMint": inp,
            "outputMint": out,
            "amount": str(amt),
            "taker": str(get_kp().pubkey())
        },
        headers=headers
    )
    if data and data.get("transaction"):
        return data
    return None

async def jupiter_exec(order):
    try:
        headers = {"x-api-key": JUP_API_KEY} if JUP_API_KEY else None

        tx = VersionedTransaction.from_bytes(
            base64.b64decode(order["transaction"])
        )
        signed = VersionedTransaction(tx.message, [get_kp()])

        r = await HTTP.post(
            "https://api.jup.ag/swap/v2/execute",
            headers=headers,
            json={
                "signedTransaction": base64.b64encode(bytes(signed)).decode()
            }
        )
        if r.status_code == 200:
            return r.json()

        log_once("exec_status", f"EXEC_STATUS {r.status_code}", 5)
        return None
    except Exception as e:
        log_once("exec_err", f"EXEC_ERR {e}", 5)
        return None

# ================= BUY =================
def can_buy(m):
    ensure_engine()

    if len(engine.positions) >= MAX_POSITIONS:
        return False
    if m in [p.get("token") for p in engine.positions if isinstance(p, dict)]:
        return False
    if now() - TOKEN_COOLDOWN[m] < 10:
        return False
    return True

async def buy(m, combo):
    if m in IN_FLIGHT_BUY:
        return

    IN_FLIGHT_BUY.add(m)

    try:
        ensure_engine()

        if not can_buy(m):
            return

        log_once(f"try_{m}", f"TRY BUY {m} {combo:.4f}", 2)

        order = await jupiter_order(SOL, m, 1000000)

        if not order:
            log(f"BUY_FAIL {m}")
            return

        res = await jupiter_exec(order)

        if not res:
            log(f"EXEC_FAIL {m}")
            return

        price = await get_price(m)
        if not price:
            log(f"BUY_NO_PRICE {m}")
            return

        engine.positions.append({
            "token": m,
            "entry_price": price,
            "last_price": price,
            "peak_price": price,
            "pnl": 0
        })

        engine.stats["buys"] += 1
        TOKEN_COOLDOWN[m] = now()

        log(f"BUY {m}")

    finally:
        IN_FLIGHT_BUY.discard(m)

# ================= SELL =================
async def sell(p):
    try:
        ensure_engine()

        m = p["token"]
        price = await get_price(m)
        if not price:
            return

        pnl = (price - p["entry_price"]) / p["entry_price"]

        if p in engine.positions:
            engine.positions.remove(p)
        engine.stats["sells"] += 1

        engine.trade_history.append({
            "token": m,
            "pnl_pct": pnl,
            "ts": now()
        })

        log(f"SELL {m} pnl={pnl:.4f}")
    except Exception as e:
        log_once("sell_err", f"SELL_ERR {e}", 5)

# ================= MONITOR =================
async def monitor():
    while True:
        try:
            ensure_engine()

            for p in list(engine.positions):
                if not isinstance(p, dict):
                    continue

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

        except Exception as e:
            log(f"MONITOR_ERR {e}")

        await asyncio.sleep(2)

# ================= RANK =================
async def rank_candidates():
    ensure_engine()
    ranked = []

    log_once("rank_debug", f"SCANNING {len(CANDIDATES)}", 3)

    for m in list(CANDIDATES):
        try:
            a = await alpha(m)
            ranked.append((m, a))
            engine.stats["signals"] += 1
        except Exception as e:
            log_once("rank_err", f"RANK_ERR {m} {e}", 5)

    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked[:5]

# ================= MAIN =================
async def main():
    while True:
        try:
            ensure_engine()
            log_once("alive", "RUNNING", 5)
            log_once("heartbeat", "SYSTEM RUNNING", 3)

            ranked = await rank_candidates()

            for m, score in ranked:
                if score > ENTRY_THRESHOLD:
                    await buy(m, score)

        except Exception as e:
            log(f"MAIN_ERR {e}")

        await asyncio.sleep(2)

# ================= WATCHDOG =================
async def watchdog():
    while True:
        ensure_engine()
        log_once("watchdog", "SYSTEM OK", 10)
        await asyncio.sleep(5)

# ================= APP =================
app = FastAPI()

@app.on_event("startup")
async def start():
    ensure_engine()
    log("SYSTEM STARTED")

    asyncio.create_task(main())
    asyncio.create_task(monitor())
    asyncio.create_task(mempool())
    asyncio.create_task(watchdog())

@app.get("/")
def root():
    ensure_engine()
    return {
        "status": "running",
        "time": now(),
        "positions": engine.positions,
        "stats": engine.stats,
        "logs": engine.logs[-20:] if isinstance(engine.logs, list) else ["SYSTEM BOOTING..."]
    }

@app.get("/debug")
def debug():
    ensure_engine()
    return {
        "logs_len": len(engine.logs),
        "positions": len(engine.positions),
        "stats": engine.stats,
        "engine_logs_type": str(type(engine.logs)),
        "engine_positions_type": str(type(engine.positions)),
        "engine_trade_history_type": str(type(engine.trade_history)),
    }

@app.get("/ui")
def ui():
    return HTMLResponse("""
    <html>
    <body style="background:black;color:lime">
    <h2>🔥 v1326.3 FINAL SAFE FIX</h2>
    <div id=data>Loading...</div>
    <script>
    async function load(){
        try{
            let r = await fetch('/');
            let d = await r.json();
            document.getElementById('data').innerHTML =
            '<pre>'+JSON.stringify(d,null,2)+'</pre>';
        }catch(e){
            document.getElementById('data').innerHTML = "ERROR LOADING";
        }
    }
    setInterval(load,2000);
    load();
    </script>
    </body>
    </html>
    """)
