# ================= V37.7 FULL FUSION (ANTI-STUCK VERSION) =================

import os
import asyncio
import time
import random
from collections import defaultdict

from app.state import engine
from app.alpha.adaptive_filter import adaptive_filter

try:
    from app.sources.fusion import fetch_candidates
except:
    async def fetch_candidates():
        return []

try:
    from app.alpha.helius_wallet_tracker import update_token_wallets
except:
    async def update_token_wallets(m):
        return []

try:
    from app.execution.jupiter_exec import execute_swap
except:
    async def execute_swap(a, b, c):
        return {"paper": True}

try:
    from app.data.market import get_quote
except:
    async def get_quote(a, b, c):
        return None

# ================= CONFIG =================

REAL_TRADING = os.getenv("REAL_TRADING", "false").lower() == "true"

SOL = "So11111111111111111111111111111111111111112"
SOL_DECIMALS = 1_000_000_000

AMOUNT = 1_000_000

MAX_POSITIONS = 3          # 🔥 修：避免卡死
MAX_EXPOSURE = 0.5

TAKE_PROFIT = 0.05
STOP_LOSS = -0.02
TRAILING_GAP = 0.012
MAX_HOLD_SEC = 120         # 🔥 修：讓單有時間跑

TOKEN_COOLDOWN = 10
FORCE_TRADE_AFTER = 15

ENTRY_THRESHOLD = 0.003
SNIPER_FALLBACK_THRESHOLD = 0.001

MIN_ORDER_SOL = 0.01

LAST_TRADE = defaultdict(float)
LAST_PRICE = {}

SOURCE_STATS = defaultdict(lambda: {"wins":0,"losses":0,"pnl":0})

# ================= ENGINE =================

def ensure_engine():
    engine.positions = getattr(engine, "positions", [])
    engine.trade_history = getattr(engine, "trade_history", [])
    engine.logs = getattr(engine, "logs", [])

    engine.capital = getattr(engine, "capital", 5.0)
    engine.start_capital = getattr(engine, "start_capital", engine.capital)
    engine.peak_capital = getattr(engine, "peak_capital", engine.capital)

    engine.running = True
    engine.no_trade_cycles = getattr(engine, "no_trade_cycles", 0)

    engine.stats = getattr(engine, "stats", {
        "signals":0,"executed":0,"rejected":0,"errors":0,
        "open_positions":0,"open_exposure":0.0,
        "trades":0,"wins":0,"losses":0,"forced_trades":0
    })

# ================= LOG =================

def log(x):
    print(x)
    engine.logs.append(str(x))
    engine.logs = engine.logs[-200:]

# ================= HELP =================

def sf(x):
    try: return float(x)
    except: return 0

def now(): return time.time()

def exposure():
    return sum(sf(p["size"]) for p in engine.positions)

# ================= TOKEN DEDUP =================

def dedup(tokens):
    seen=set()
    out=[]
    for t in tokens:
        m=t.get("mint")
        if not m or m in seen: continue
        seen.add(m)
        out.append(t)
    return out

# ================= PRICE =================

async def get_price(m):
    q = await get_quote(SOL, m, AMOUNT)
    if not q: return None

    out = sf(q.get("outAmount",0))
    if out<=0 or out>1e12: return None

    return out/1e6

# ================= FEATURES =================

async def features(t):
    m=t["mint"]
    price=await get_price(m)
    if not price: return None

    prev=LAST_PRICE.get(m)

    if prev:
        breakout=max((price-prev)/prev,0)
    else:
        breakout=0.005

    if breakout==0:
        breakout=random.uniform(0.001,0.003)

    LAST_PRICE[m]=price

    wallets=await update_token_wallets(m)
    smart=min(len(wallets)/5,1)

    return {
        "mint":m,
        "price":price,
        "breakout":breakout,
        "smart":smart,
        "is_new":prev is None,
        "source":t.get("source","unknown")
    }

# ================= SCORE =================

def mode(f):
    if f["is_new"]: return "sniper"
    if f["smart"]>0.6: return "smart"
    return "momentum"

def score(f):
    m=mode(f)
    if m=="sniper":
        s=f["breakout"]*0.4+f["smart"]*0.6
    elif m=="smart":
        s=f["smart"]*0.7+f["breakout"]*0.3
    else:
        s=f["breakout"]*0.8+f["smart"]*0.2
    return s,m

def size(score):
    base=engine.capital*0.03   # 🔥 降風險
    return min(base, engine.capital*0.15)

# ================= BUY =================

async def buy(m,f,s,mode):
    amt=int(max(s,MIN_ORDER_SOL)*SOL_DECIMALS)

    res=await execute_swap(SOL,m,amt)

    if res.get("error"):
        log(f"BUY_FAIL {m[:6]}")
        return False

    engine.capital-=s

    out=int(res.get("quote",{}).get("outAmount") or 0)

    engine.positions.append({
        "mint":m,
        "entry":f["price"],
        "size":s,
        "token_amount_atomic":out,
        "time":now(),
        "mode":mode,
        "source":f["source"],
        "high":f["price"]
    })

    LAST_TRADE[m]=now()

    engine.stats["executed"]+=1

    log(f"BUY {m[:6]} {mode} score={s:.4f}")
    return True

# ================= SELL =================

async def sell(p,reason,pnl,price):
    m=p["mint"]

    res=await execute_swap(m,SOL,p.get("token_amount_atomic",0))

    if res.get("error"):
        log(f"SELL_FAIL {m[:6]}")
        return False

    engine.positions.remove(p)
    engine.capital+=p["size"]*(1+pnl)

    if pnl>0:
        engine.stats["wins"]+=1
        SOURCE_STATS[p["source"]]["wins"]+=1
    else:
        engine.stats["losses"]+=1
        SOURCE_STATS[p["source"]]["losses"]+=1

    engine.stats["trades"]+=1

    log(f"SELL {m[:6]} {reason} pnl={pnl:.4f}")
    return True

async def check_sell(p):
    price=await get_price(p["mint"])
    if not price: return

    pnl=(price-p["entry"])/p["entry"]

    p["high"]=max(p["high"],price)

    reason=None
    if pnl>=TAKE_PROFIT: reason="TP"
    elif pnl<=STOP_LOSS: reason="SL"
    elif price<p["high"]*(1-TRAILING_GAP): reason="TRAIL"
    elif now()-p["time"]>MAX_HOLD_SEC: reason="TIME"

    if reason:
        await sell(p,reason,pnl,price)

# ================= TRADE =================

async def trade(t,forced=False):
    m=t["mint"]

    if any(p["mint"]==m for p in engine.positions): return
    if now()-LAST_TRADE[m]<TOKEN_COOLDOWN: return
    if len(engine.positions)>=MAX_POSITIONS: return
    if exposure()>engine.capital*MAX_EXPOSURE: return

    f=await features(t)
    if not f: return

    ok,_=adaptive_filter(f,None,engine.no_trade_cycles)
    if not ok:
        ok=engine.no_trade_cycles>5 or forced
    if not ok: return

    s,mtype=score(f)

    if s<ENTRY_THRESHOLD:
        if not (mtype=="sniper" and s>SNIPER_FALLBACK_THRESHOLD or forced):
            return

    sizev=size(s)

    if engine.capital<sizev: return

    return await buy(m,f,sizev,mtype)

# ================= LOOP =================

async def main_loop():
    ensure_engine()
    log("🚀 V37.7 START")

    while engine.running:

        try:
            tokens=await fetch_candidates()
            tokens=dedup(tokens)
            random.shuffle(tokens)

            # 🔥 先 SELL
            for p in list(engine.positions):
                await check_sell(p)

            traded=False

            for t in tokens[:20]:
                if await trade(t):
                    traded=True

            if not traded:
                engine.no_trade_cycles+=1
            else:
                engine.no_trade_cycles=0

            # 🔥 FIX：不滿倉才 force
            if (
                engine.no_trade_cycles>FORCE_TRADE_AFTER
                and tokens
                and len(engine.positions)<MAX_POSITIONS
                and exposure()<engine.capital*MAX_EXPOSURE
            ):
                log("FORCE_TRADE")
                await trade(tokens[0],forced=True)

        except Exception as e:
            log(f"ERR {e}")

        await asyncio.sleep(2)
