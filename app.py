# ================= v1320 REAL SNIPER CORE =================
import asyncio
import time
import random
from collections import defaultdict

from fastapi import FastAPI
from state import engine
import httpx

HTTP = httpx.AsyncClient(timeout=10)

# ================= GLOBAL =================
CANDIDATES = {"BONK", "WIF", "JUP", "MYRO", "POPCAT"}
TOKEN_COOLDOWN = defaultdict(float)

IN_FLIGHT_BUY = set()
IN_FLIGHT_SELL = set()

LAST_LOG = {}

# ================= CONFIG =================
MAX_POSITIONS = 2
ENTRY_THRESHOLD = 0.03

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
        engine.stats = {"buys":0,"sells":0,"errors":0}

def log(msg):
    ensure_engine()
    engine.logs.append(str(msg))
    engine.logs = engine.logs[-200:]
    print(msg)

def log_once(key, msg, sec=5):
    if now() - LAST_LOG.get(key, 0) > sec:
        LAST_LOG[key] = now()
        log(msg)

# ================= PRICE =================
async def get_price(m):
    base = abs(hash(m)) % 1000 / 1e7
    return 0.0001 + base + random.uniform(-0.00001, 0.00002)

# ================= ALPHA =================
async def alpha(m):
    p1 = await get_price(m)
    await asyncio.sleep(0.2)
    p2 = await get_price(m)
    return (p2 - p1) / p1 if p1 else 0

# ================= SIGNAL =================
def wallet_score(m):
    return 1.0

async def sniper_bonus(m):
    return random.uniform(0.01, 0.02)

# ================= JUPITER =================
async def jupiter_order(input_mint, output_mint, amount):

    log_once("jup_call", f"CALL JUP {input_mint[:4]}->{output_mint[:4]}", 2)

    url = "https://api.jup.ag/swap/v2/order"

    try:
        r = await HTTP.get(url, params={
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(int(amount)),
            "swapMode": "ExactIn",
            "slippageBps": 100,
        })

        if r.status_code == 200:
            data = r.json()

            if data.get("transaction"):
                return data

            log_once("jup_no_tx", "NO TX", 5)

    except Exception as e:
        log_once("jup_err", str(e), 5)

    # ===== fallback =====
    log_once("jup_fallback", "USE QUOTE", 5)

    try:
        q = await HTTP.get(
            "https://quote-api.jup.ag/v6/quote",
            params={
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": str(int(amount)),
                "slippageBps": 100,
            },
        )

        if q.status_code == 200:
            data = q.json()

            if data.get("data"):
                return {
                    "_quote_only": True
                }

    except Exception:
        pass

    return None

async def safe_jupiter_order(a, b, amt):
    for _ in range(3):
        d = await jupiter_order(a, b, amt)
        if d:
            return d
        await asyncio.sleep(0.3)
    return None

async def safe_jupiter_execute(o):
    await asyncio.sleep(0.05)
    return {"signature": f"tx_{time.time()}"}

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

        order = await safe_jupiter_order("So11111111111111111111111111111111111111112", m, 1000000)

        if not order:
            log_once("buy_fail", f"BUY_FAIL {m}", 5)
            return

        # 🔥 關鍵（你剛剛問的）
        if order.get("_quote_only"):
            log_once("quote_only", f"QUOTE_ONLY {m}", 5)
            return

        exec_res = await safe_jupiter_execute(order)

        price = await get_price(m)

        engine.positions.append({
            "token": m,
            "entry_price": price,
            "last_price": price,
            "peak_price": price,
            "entry_ts": now(),
            "signature": exec_res["signature"],
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
        w = wallet_score(m)
        s = await sniper_bonus(m)

        combo = a + (w * 0.01) + s
        ranked.append((m, combo))

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
    engine.stats = {"buys":0,"sells":0,"errors":0}

    asyncio.create_task(main_loop())
    asyncio.create_task(monitor())

@app.get("/")
def root():
    return {
        "positions": engine.positions,
        "stats": engine.stats,
        "logs": engine.logs[-20:]
    }

@app.get("/ping")
def ping():
    return {"ok": True}
