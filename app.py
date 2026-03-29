# ================= v1318 SEMI-LIVE TRADING =================
import asyncio
import time
import random
from collections import defaultdict

from fastapi import FastAPI
from state import engine

# ================= GLOBAL =================
CANDIDATES = set()
TOKEN_COOLDOWN = defaultdict(float)

IN_FLIGHT_BUY = set()
IN_FLIGHT_SELL = set()

LAST_LOG = {}

# ================= CONFIG =================
MAX_POSITIONS = 2
BASE_SIZE = 0.0015

ENTRY_THRESHOLD = 0.03
MIN_THRESHOLD = 0.015
MAX_THRESHOLD = 0.08

TAKE_PROFIT = 0.12
STOP_LOSS = -0.05

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
        engine.stats = {"buys": 0, "sells": 0, "errors": 0}

def log(msg):
    ensure_engine()
    engine.logs.append(str(msg))
    engine.logs = engine.logs[-200:]
    print(msg)

def log_once(key, msg, sec=10):
    if now() - LAST_LOG.get(key, 0) > sec:
        LAST_LOG[key] = now()
        log(msg)

# ================= PRICE（模擬波動） =================
PRICE_CACHE = {}

async def get_price(m):
    base = abs(hash(m)) % 1000 / 1e7

    noise = (time.time() % 1) * 0.00002
    drift = random.uniform(-0.00001, 0.00002)

    price = 0.0001 + base + noise + drift

    PRICE_CACHE[m] = price
    return price

# ================= ALPHA =================
async def alpha(m):
    p1 = await get_price(m)
    await asyncio.sleep(0.2)
    p2 = await get_price(m)

    if not p1 or not p2:
        return 0

    return (p2 - p1) / p1

# ================= SIGNAL =================
def wallet_score(m):
    return 1.0

async def sniper_bonus(m):
    return random.uniform(0.005, 0.02)

# ================= RANK =================
async def rank_candidates():
    pool = list(CANDIDATES)
    ranked = []

    for m in pool[:10]:
        try:
            a = await alpha(m)
            w = wallet_score(m)
            s = await sniper_bonus(m)

            combo = a + (w * 0.01) + s

            ranked.append((m, combo, a, w, s))

        except Exception:
            continue

    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked[:5]

# ================= ORDER =================
async def safe_order():
    await asyncio.sleep(0.05)
    return True

# ================= BUY =================
def can_buy(m):
    if len(engine.positions) >= MAX_POSITIONS:
        return False

    if m in [p["token"] for p in engine.positions]:
        return False

    if now() - TOKEN_COOLDOWN[m] < 20:
        return False

    return True

def position_size(combo):
    if combo > 0.06:
        return BASE_SIZE * 1.5
    elif combo > 0.04:
        return BASE_SIZE
    return BASE_SIZE * 0.6

async def buy(m, combo):
    if m in IN_FLIGHT_BUY:
        return

    IN_FLIGHT_BUY.add(m)

    try:
        if not can_buy(m):
            return

        ok = await safe_order()
        if not ok:
            return

        price = await get_price(m)
        size = position_size(combo)

        engine.positions.append({
            "token": m,
            "entry_price": price,
            "last_price": price,
            "peak_price": price,
            "entry_ts": now(),
            "size": size,
            "combo": combo,
            "pnl_pct": 0,
        })

        TOKEN_COOLDOWN[m] = now()
        engine.stats["buys"] += 1

        log(f"BUY {m} combo={combo:.4f} size={size:.4f}")

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

        if p in engine.positions:
            engine.positions.remove(p)

        engine.trade_history.append({
            "token": m,
            "pnl_pct": pnl,
            "ts": now(),
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

                if pnl > TAKE_PROFIT:
                    await sell(p)
                    continue

                if pnl < STOP_LOSS or drawdown < STOP_LOSS:
                    await sell(p)

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"MONITOR_ERR {e}")

        await asyncio.sleep(2)

# ================= AI（自動調參） =================
async def ai_loop():
    global ENTRY_THRESHOLD

    while True:
        try:
            trades = engine.trade_history[-20:]

            if trades:
                avg = sum(t["pnl_pct"] for t in trades) / len(trades)

                if avg > 0:
                    ENTRY_THRESHOLD *= 0.95
                else:
                    ENTRY_THRESHOLD *= 1.05

                ENTRY_THRESHOLD = max(MIN_THRESHOLD, min(MAX_THRESHOLD, ENTRY_THRESHOLD))

                log_once("ai", f"AI threshold={ENTRY_THRESHOLD:.4f}", 15)

        except Exception:
            pass

        await asyncio.sleep(10)

# ================= MAIN =================
async def main_loop():
    while True:
        try:
            ranked = await rank_candidates()

            log_once("rank", f"RANKED {len(ranked)}", 5)

            for m, combo, *_ in ranked:
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
    engine.stats = {"buys": 0, "sells": 0, "errors": 0}

    CANDIDATES.update({"BONK", "WIF", "JUP", "MYRO", "POPCAT"})

    asyncio.create_task(main_loop())
    asyncio.create_task(monitor())
    asyncio.create_task(ai_loop())

@app.get("/")
def root():
    return {
        "positions": engine.positions,
        "stats": engine.stats,
        "threshold": ENTRY_THRESHOLD,
        "logs": engine.logs[-20:]
    }

@app.get("/status")
def status():
    return {
        "positions": engine.positions,
        "trades": engine.trade_history[-10:],
        "threshold": ENTRY_THRESHOLD,
        "logs": engine.logs[-50:]
    }
