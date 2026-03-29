# ================= v1317 DEBUG FIXED APP =================
import asyncio
import time
from collections import defaultdict

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from state import engine

# ================= GLOBAL =================
CANDIDATES = set()
CANDIDATE_META = {}
TOKEN_COOLDOWN = defaultdict(float)

IN_FLIGHT_BUY = set()
IN_FLIGHT_SELL = set()

SMART_MONEY = {}
SMART_MONEY_SCORE = {}

LAST_LOG = {}

# ================= CONFIG =================
MAX_POSITIONS = 5
MIN_POSITION_SOL = 0.001
MAX_POSITION_SOL = 0.003
BUY_SIZE_SOL = 0.0015

# ================= UTIL =================
def now():
    return time.time()

def ensure_engine():
    if not hasattr(engine, "positions") or not isinstance(engine.positions, list):
        engine.positions = []
    if not hasattr(engine, "trade_history") or not isinstance(engine.trade_history, list):
        engine.trade_history = []
    if not hasattr(engine, "logs") or not isinstance(engine.logs, list):
        engine.logs = []
    if not hasattr(engine, "stats") or not isinstance(engine.stats, dict):
        engine.stats = {"buys": 0, "sells": 0, "errors": 0}
    if not hasattr(engine, "candidate_count"):
        engine.candidate_count = 0

def log(msg):
    ensure_engine()
    engine.logs.append(str(msg))
    engine.logs = engine.logs[-200:]
    print(msg)

def log_once(key, msg, sec=10):
    if now() - LAST_LOG.get(key, 0) > sec:
        LAST_LOG[key] = now()
        log(msg)

# ================= MOCK PRICE =================
async def get_price(m):
    # 穩定一點的假價格
    base = abs(hash(m)) % 1000
    return 0.0001 + base / 1e7

# ================= ALPHA（加速版） =================
async def alpha(m):
    p1 = await get_price(m)
    if not p1:
        return 0.0

    await asyncio.sleep(0.15)

    p2 = await get_price(m)
    if not p2:
        return 0.0

    return (p2 - p1) / p1

# ================= SIGNAL =================
def wallet_score(m):
    return 1.0

async def sniper_bonus(m):
    return 0.01

# ================= SMART MONEY =================
def update_smart_money():
    ensure_engine()

    for t in engine.trade_history[-100:]:
        w = t.get("wallet")
        pnl = t.get("pnl_pct", 0)

        if not w:
            continue

        if w not in SMART_MONEY:
            SMART_MONEY[w] = {"pnl": 0, "trades": 0}

        SMART_MONEY[w]["pnl"] += pnl
        SMART_MONEY[w]["trades"] += 1

    for w, s in SMART_MONEY.items():
        if s["trades"] >= 3:
            SMART_MONEY_SCORE[w] = max(0.5, min(3.0, 1 + s["pnl"]))

def smart_money_score(m):
    return 1.0

def insider_score(m):
    return 0.02

def cluster_score(m):
    return 0.02

def fake_pump_filter(m, a, w, s):
    return not (a > 0.05 and w < 1.1)

# ================= RANK =================
async def rank_candidates():
    update_smart_money()

    pool = list(CANDIDATES)
    ranked = []

    for m in pool[:15]:
        try:
            a = await alpha(m)
            w = wallet_score(m)
            s = await sniper_bonus(m)

            combo = a + (w * 0.01) + s
            combo += (smart_money_score(m) * 0.01)
            combo += insider_score(m)
            combo += cluster_score(m)

            if not fake_pump_filter(m, a, w, s):
                continue

            ranked.append((m, combo, a, w, s))

        except Exception as e:
            log_once(f"rank_err_{m}", f"RANK_ERR {m} {e}", 10)
            continue

    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked[:10]

# ================= JUP MOCK =================
async def jupiter_order(a, b, amt):
    return {
        "transaction": "ok",
        "outAmount": amt * 2
    }

async def safe_jupiter_order(a, b, amt):
    for _ in range(3):
        d = await jupiter_order(a, b, amt)
        if d and d.get("transaction"):
            return d
        await asyncio.sleep(0.2)
    return None

async def safe_jupiter_execute(o):
    return {"signature": "tx_" + str(time.time())}

# ================= BUY =================
def can_buy(m):
    ensure_engine()

    if len(engine.positions) >= MAX_POSITIONS:
        return False
    if m in [p["token"] for p in engine.positions]:
        return False
    if now() - TOKEN_COOLDOWN[m] < 5:
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

        order = await safe_jupiter_order("SOL", m, 1000)
        if not order:
            log_once(f"buy_fail_{m}", f"BUY_FAIL {m}", 5)
            return

        exec_res = await safe_jupiter_execute(order)
        entry_price = await get_price(m)

        pos = {
            "token": m,
            "entry_price": entry_price,
            "last_price": entry_price,
            "peak_price": entry_price,
            "entry_ts": now(),
            "signature": exec_res["signature"],
            "combo": combo,
            "pnl_pct": 0.0,
        }

        engine.positions.append(pos)
        TOKEN_COOLDOWN[m] = now()
        engine.stats["buys"] += 1
        log(f"BUY {m} combo={combo:.4f}")

    finally:
        IN_FLIGHT_BUY.discard(m)

# ================= SELL =================
async def sell(p):
    m = p["token"]

    if m in IN_FLIGHT_SELL:
        return

    IN_FLIGHT_SELL.add(m)

    try:
        ensure_engine()

        exit_price = await get_price(m)
        pnl = 0.0
        if p.get("entry_price"):
            pnl = (exit_price - p["entry_price"]) / p["entry_price"]

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

# ================= TAKE PROFIT =================
async def take_profit_logic(p):
    price = await get_price(p["token"])
    if not price:
        return False

    pnl = (price - p["entry_price"]) / p["entry_price"]

    if pnl >= 0.10:
        return True

    return False

# ================= MONITOR =================
async def monitor_loop():
    while True:
        try:
            ensure_engine()

            for p in list(engine.positions):
                price = await get_price(p["token"])
                if not price:
                    continue

                pnl = (price - p["entry_price"]) / p["entry_price"]
                peak = max(p.get("peak_price", price), price)

                p["last_price"] = price
                p["peak_price"] = peak
                p["pnl_pct"] = pnl

                drawdown = (price - peak) / peak if peak else 0.0

                if await take_profit_logic(p):
                    await sell(p)
                    continue

                if pnl <= -0.05 or drawdown <= -0.05:
                    await sell(p)

        except Exception as e:
            ensure_engine()
            engine.stats["errors"] += 1
            log_once("monitor_err", f"MONITOR_ERR {e}", 10)

        await asyncio.sleep(2)

# ================= LOOP =================
async def main_loop():
    while True:
        try:
            ensure_engine()

            engine.candidate_count = len(CANDIDATES)
            log_once("cand", f"CANDIDATE_COUNT {engine.candidate_count}", 5)

            ranked = await rank_candidates()
            log_once("rank", f"RANKED_COUNT {len(ranked)}", 5)

            for m, combo, a, w, s in ranked:
                log_once(f"rank_{m}", f"RANK {m} combo={combo:.4f} a={a:.4f} w={w:.2f} s={s:.4f}", 8)
                if combo > 0:
                    await buy(m, combo)

        except Exception as e:
            ensure_engine()
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

    # 測試候選
    CANDIDATES.clear()
    CANDIDATES.update({"BONK", "JUP", "WIF", "MYRO", "POPCAT"})

    asyncio.create_task(main_loop())
    asyncio.create_task(monitor_loop())

@app.get("/")
def root():
    ensure_engine()
    return {
        "ok": True,
        "positions": engine.positions,
        "stats": engine.stats,
        "candidates": len(CANDIDATES),
        "logs": engine.logs[-20:],
    }

@app.get("/status")
def status():
    ensure_engine()
    return {
        "ok": True,
        "positions": engine.positions,
        "stats": engine.stats,
        "candidate_count": len(CANDIDATES),
        "trade_history": engine.trade_history[-20:],
        "logs": engine.logs[-50:],
    }

@app.get("/add/{mint}")
def add_candidate(mint: str):
    CANDIDATES.add(mint)
    CANDIDATE_META[mint] = {"added_at": now()}
    return {"ok": True, "mint": mint, "candidate_count": len(CANDIDATES)}

@app.get("/ping")
def ping():
    return {"pong": True}
