# ================= V33.1 TRUE FUSION =================

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
MAX_EXPOSURE = 0.45
MAX_POSITION_SIZE = 0.18

TAKE_PROFIT = 0.06
STOP_LOSS = -0.02
TRAILING_GAP = 0.012
MAX_HOLD_SEC = 45

TOKEN_COOLDOWN = 10

SOL = "So11111111111111111111111111111111111111112"
AMOUNT = 1_000_000

LAST_TRADE = defaultdict(float)
LAST_PRICE = {}

# 🧠 FUND MEMORY
SOURCE_STATS = defaultdict(lambda: {
    "wins": 0,
    "losses": 0,
    "pnl": 0.0,
})

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
async def safe_quote(i, o, a):
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
    q = await safe_quote(SOL, m, AMOUNT)
    if not q: return None
    out = sf(q.get("outAmount",0))
    if out <= 0: return None
    return out / 1e6, q

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

    breakout = 0
    if prev:
        breakout = max((price-prev)/prev,0)

    # ⭐ fallback 修正
    if prev is None:
        breakout = 0.008

    # ⭐ fallback source 放寬
    if src in ("dex","helius") and breakout < 0.003:
        breakout = 0.003

    LAST_PRICE[m] = price

    liq = sf(q.get("outAmount",0)) / 1e5

    if breakout < 0.004:
        return None

    if liq < 0.002:
        return None

    if len(wallets) < 1:
        return None

    return {
        "mint": m,
        "source": src,
        "breakout": breakout,
        "smart_money": min(len(wallets)/6,1),
        "liquidity": liq,
        "price": price
    }

# ================= FUND BRAIN =================
def source_weight(src):
    s = SOURCE_STATS[src]
    total = s["wins"] + s["losses"]

    if total < 3:
        return 1.0

    winrate = s["wins"] / total

    if winrate > 0.6:
        return 1.2
    elif winrate < 0.3:
        return 0.7

    return 1.0

# ================= SCORE =================
def score_alpha(f):
    base = (
        f["breakout"] * 0.5 +
        f["smart_money"] * 0.3 +
        f["liquidity"] * 0.2
    )
    return base * source_weight(f["source"])

# ================= SIZE =================
def size(score):
    base = engine.capital * 0.08
    if score > 0.5:
        base *= 1.3
    return min(base, engine.capital * MAX_POSITION_SIZE)

# ================= SELL =================
async def check_sell(p):
    data = await get_price(p["mint"])
    if not data:
        return

    price,_ = data
    pnl = (price - p["entry"]) / p["entry"]
    held = time.time() - p["time"]

    reason = None

    if pnl >= TAKE_PROFIT:
        reason = "TP"
    elif pnl <= STOP_LOSS:
        reason = "SL"
    elif pnl < p.get("peak",0) - TRAILING_GAP:
        reason = "TRAIL"
    elif held > MAX_HOLD_SEC and pnl < 0.01:
        reason = "TIME"

    if pnl > p.get("peak",0):
        p["peak"] = pnl

    if not reason:
        return

    engine.positions.remove(p)
    engine.capital += p["size"] * (1+pnl)

    src = p["source"]
    if pnl > 0:
        SOURCE_STATS[src]["wins"] += 1
    else:
        SOURCE_STATS[src]["losses"] += 1

    SOURCE_STATS[src]["pnl"] += pnl

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

    if exposure() > engine.capital * MAX_EXPOSURE:
        return False

    f = await features(t)
    if not f:
        return False

    # ⭐ filter bypass（冷啟動）
    ok,_ = adaptive_filter(f,None,engine.no_trade_cycles)
    if not ok and engine.no_trade_cycles < 10:
        ok = True

    if not ok:
        return False

    score = score_alpha(f)

    if score < 0.20:
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
        "peak": 0.0,
        "source": f["source"]
    })

    LAST_TRADE[m] = time.time()

    log(f"BUY {m[:6]} score={score:.3f}")

    return True

# ================= LOOP =================
async def main_loop():
    ensure_engine()
    log("🔥 V33.1 TRUE FUSION START")

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
