# ================= V34 FUND MODE =================

import asyncio
import time
from collections import defaultdict

from app.state import engine
from app.alpha.adaptive_filter import adaptive_filter

try:
    from app.sources.fusion import fetch_candidates
except:
    async def fetch_candidates():
        return []

try:
    from app.data.market import get_quote
except:
    async def get_quote(a,b,c): return None

# ================= CONFIG =================
MIN_SCORE = 0.55
MIN_LIQ = 0.01
COOLDOWN = 8

SOL = "So11111111111111111111111111111111111111112"
AMOUNT = 1_000_000

LAST_TRADE = defaultdict(float)
LAST_PRICE = {}

# 🧠 FUND BRAIN
SOURCE_STATS = defaultdict(lambda:{
    "wins":0,
    "losses":0,
    "pnl":0.0,
    "disabled":False
})


# ================= ENGINE =================
def log(x):
    print(x)
    engine.logs.append(str(x))
    engine.logs = engine.logs[-200:]


# ================= PRICE =================
async def price(m):
    q = await get_quote(SOL,m,AMOUNT)
    if not q: return None
    out = float(q.get("outAmount",0))
    if out <= 0: return None
    return out/1e6


# ================= REGIME =================
def market_ok():
    if engine.no_trade_cycles < 5:
        return False
    return True


# ================= SOURCE FILTER =================
def source_ok(src):
    s = SOURCE_STATS[src]

    total = s["wins"] + s["losses"]

    if total < 5:
        return True

    winrate = s["wins"]/total

    if winrate < 0.4:
        s["disabled"] = True

    return not s["disabled"]


# ================= FEATURES =================
async def features(t):
    m = t["mint"]
    src = t.get("source","unknown")

    if not source_ok(src):
        log(f"KILL_SOURCE {src}")
        return None

    p = await price(m)
    if not p:
        return None

    prev = LAST_PRICE.get(m)

    if prev:
        breakout = (p-prev)/prev
    else:
        breakout = 0.01

    LAST_PRICE[m] = p

    if breakout < 0.01:
        return None

    return {
        "mint":m,
        "price":p,
        "breakout":breakout,
        "source":src
    }


# ================= SCORE =================
def score(f):
    return min(f["breakout"]*5,1)


# ================= SIZE =================
def size(score):
    base = engine.capital * 0.06

    if score > 0.7:
        base *= 2

    return min(base, engine.capital*0.2)


# ================= SELL =================
async def check_sell(p):
    pr = await price(p["mint"])
    if not pr: return

    pnl = (pr - p["entry"])/p["entry"]

    if pnl > 0.08 or pnl < -0.1:
        engine.positions.remove(p)
        engine.capital += p["size"]*(1+pnl)

        s = SOURCE_STATS[p["source"]]

        if pnl > 0:
            s["wins"]+=1
        else:
            s["losses"]+=1

        s["pnl"] += pnl

        log(f"SELL {p['mint'][:6]} pnl={pnl:.4f}")


# ================= TRADE =================
async def trade(t):
    m = t["mint"]

    if time.time() - LAST_TRADE[m] < COOLDOWN:
        return False

    if len(engine.positions) >= 3:
        return False

    f = await features(t)
    if not f:
        return False

    ok,_ = adaptive_filter(f,None,engine.no_trade_cycles)
    if not ok:
        return False

    sc = score(f)

    if sc < MIN_SCORE:
        return False

    s = size(sc)

    if engine.capital < s:
        return False

    engine.capital -= s

    engine.positions.append({
        "mint":m,
        "entry":f["price"],
        "size":s,
        "time":time.time(),
        "source":f["source"]
    })

    LAST_TRADE[m] = time.time()

    log(f"BUY {m[:6]} score={sc:.2f}")

    return True


# ================= LOOP =================
async def main_loop():
    log("🔥 V34 FUND MODE START")

    while engine.running:

        try:
            if not market_ok():
                await asyncio.sleep(3)
                continue

            tokens = await fetch_candidates()

            for t in tokens:
                await trade(t)

            for p in list(engine.positions):
                await check_sell(p)

        except Exception as e:
            log(f"ERR {e}")

        await asyncio.sleep(2)
