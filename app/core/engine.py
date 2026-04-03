# ================= V35.2 TRUE FUND ENGINE =================

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
    from app.data.market import get_quote, looks_like_solana_mint
except:
    async def get_quote(a,b,c): return None
    def looks_like_solana_mint(x): return True

try:
    from app.alpha.helius_wallet_tracker import update_token_wallets
except:
    async def update_token_wallets(m): return []

# ================= CONFIG =================
MAX_POSITIONS = 3
MAX_EXPOSURE = 0.5
MAX_POSITION_SIZE = 0.25

TAKE_PROFIT = 0.06
STOP_LOSS = -0.025
TRAILING_GAP = 0.015
MAX_HOLD_SEC = 60

TOKEN_COOLDOWN = 10

SOL = "So11111111111111111111111111111111111111112"
AMOUNT = 1_000_000

LAST_TRADE = defaultdict(float)
LAST_PRICE = {}

# ================= ENGINE =================
def ensure_engine():
    engine.positions = getattr(engine, "positions", [])
    engine.trade_history = getattr(engine, "trade_history", [])
    engine.logs = getattr(engine, "logs", [])
    engine.capital = getattr(engine, "capital", 5.0)
    engine.peak_capital = getattr(engine, "peak_capital", engine.capital)
    engine.running = True
    engine.no_trade_cycles = getattr(engine, "no_trade_cycles", 0)

# ================= LOG =================
def log(msg):
    print(msg)
    engine.logs.append(str(msg))
    engine.logs = engine.logs[-300:]

# ================= HELP =================
def sf(x):
    try:
        return float(x)
    except:
        return 0.0

def exposure():
    return sum(sf(p["size"]) for p in engine.positions)

# ================= PRICE =================
async def safe_quote(i,o,a):
    for _ in range(3):
        try:
            q = await get_quote(i,o,a)
            if q and q.get("outAmount"):
                return q
        except:
            pass
        await asyncio.sleep(0.2)
    return None

async def get_price(m):
    q = await safe_quote(SOL,m,AMOUNT)
    if not q: return None
    out = sf(q.get("outAmount",0))
    if out <= 0: return None
    return out/1e6, q

# ================= FEATURES =================
async def features(t):
    m = t["mint"]
    src = t.get("source","unknown")

    wallets = await update_token_wallets(m)
    data = await get_price(m)
    if not data:
        return None

    price, q = data
    prev = LAST_PRICE.get(m)

    # ===== strategy mode =====
    wallet_count = len(wallets)
    if wallet_count >= 2:
        mode = "smart"
    else:
        mode = "momentum"

    breakout = 0.0

    if prev:
        breakout = max((price-prev)/prev,0)

    # ⭐ 首次進場修正
    if prev is None:
        breakout = 0.01

    LAST_PRICE[m] = price

    liq = sf(q.get("outAmount",0)) / 1e5

    # ===== liquidity 分模式 =====
    if mode == "momentum":
        liq = max(liq, 0.002)
        MIN_LIQ = 0.0005
    else:
        MIN_LIQ = 0.003

    if liq < MIN_LIQ:
        log(f"FEATURE_LIQ_FAIL {m[:6]} liq={liq:.6f}")
        return None

    # ===== breakout =====
    if mode == "momentum":
        if breakout < 0.003:
            breakout = 0.006
    else:
        if breakout < 0.005:
            log(f"FEATURE_BREAKOUT_FAIL {m[:6]} breakout={breakout:.6f}")
            return None

    # ===== impact（只對 smart）=====
    if mode == "smart" and q.get("source") != "dexscreener":
        if prev and prev > 0:
            impact = abs(price-prev)/prev
            if impact > 0.5:
                log(f"FEATURE_IMPACT_FAIL {m[:6]} impact={impact:.4f}")
                return None

    return {
        "mint": m,
        "source": src,
        "breakout": breakout,
        "smart_money": min(wallet_count/5,1),
        "liquidity": liq,
        "price": price,
        "strategy_mode": mode
    }

# ================= SCORE =================
def score_alpha(f):
    return (
        f["breakout"] * 0.5 +
        f["smart_money"] * 0.3 +
        f["liquidity"] * 0.2
    )

# ================= SIZE =================
def size(score):
    base = engine.capital * 0.1
    if score > 0.5:
        base *= 1.5
    return min(base, engine.capital * MAX_POSITION_SIZE)

# ================= SELL =================
async def check_sell(p):
    data = await get_price(p["mint"])
    if not data:
        return

    price,_ = data
    pnl = (price - p["entry"]) / p["entry"]

    reason = None
    if pnl >= TAKE_PROFIT:
        reason = "TP"
    elif pnl <= STOP_LOSS:
        reason = "SL"

    if not reason:
        return

    engine.positions.remove(p)
    engine.capital += p["size"] * (1+pnl)

    log(f"SELL {p['mint'][:6]} {reason} pnl={pnl:.4f}")

# ================= TRADE =================
async def trade(t):
    m = t["mint"]

    if any(p["mint"] == m for p in engine.positions):
        return False

    if time.time() - LAST_TRADE[m] < TOKEN_COOLDOWN:
        return False

    if len(engine.positions) >= MAX_POSITIONS:
        return False

    f = await features(t)
    if not f:
        return False

    mode = f.get("strategy_mode","momentum")

    # ===== adaptive filter =====
    ok = True
    if mode == "smart":
        try:
            ok,_ = adaptive_filter(f,None,engine.no_trade_cycles)
        except:
            ok = True
    else:
        ok = True
        log(f"FILTER_BYPASS_MOMENTUM {m[:6]}")

    if not ok:
        return False

    score = score_alpha(f)

    if mode == "momentum":
        MIN_SCORE = 0.45
    else:
        MIN_SCORE = 0.22

    if score < MIN_SCORE:
        return False

    s = size(score)

    if engine.capital < s:
        return False

    engine.capital -= s

    engine.positions.append({
        "mint": m,
        "entry": f["price"],
        "size": s,
        "time": time.time(),
        "source": f["source"]
    })

    LAST_TRADE[m] = time.time()

    log(f"BUY {m[:6]} {mode} score={score:.3f}")

    return True

# ================= LOOP =================
async def main_loop():
    ensure_engine()
    log("🚀 V35.2 FUND ENGINE START")

    while engine.running:

        try:
            tokens = await fetch_candidates()

            for t in tokens:
                await trade(t)

            for p in list(engine.positions):
                await check_sell(p)

        except Exception as e:
            log(f"ERR {e}")

        await asyncio.sleep(2)
