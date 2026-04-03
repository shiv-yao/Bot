# ================= V36.1 TRUE FUSION FUND =================

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
    from app.data.market import get_quote
except:
    async def get_quote(a,b,c): return None

try:
    from app.alpha.helius_wallet_tracker import update_token_wallets
except:
    async def update_token_wallets(m): return []

# ================= CONFIG =================
MAX_POSITIONS = 2
MAX_EXPOSURE = 0.5
MAX_POSITION_SIZE = 0.2

TAKE_PROFIT = 0.08
STOP_LOSS = -0.15

TOKEN_COOLDOWN = 10

SOL = "So11111111111111111111111111111111111111112"
AMOUNT = 1_000_000

LAST_PRICE = {}
LAST_TRADE = defaultdict(float)

# 🧠 學習系統
SOURCE_STATS = defaultdict(lambda: {"wins":0,"losses":0})

# ================= ENGINE =================
def ensure_engine():
    engine.positions = getattr(engine, "positions", [])
    engine.logs = getattr(engine, "logs", [])
    engine.capital = getattr(engine, "capital", 5.0)
    engine.running = True

def log(msg):
    print(msg)
    engine.logs.append(str(msg))
    engine.logs = engine.logs[-300:]

def sf(x):
    try: return float(x)
    except: return 0.0

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
    if not data: return None

    price,_ = data
    prev = LAST_PRICE.get(m)

    breakout = 0
    if prev:
        breakout = max((price-prev)/prev,0)

    if prev is None:
        breakout = 0.01

    LAST_PRICE[m] = price

    wallet_count = len(wallets)

    # ⭐ 模式判斷
    if wallet_count >= 2:
        mode = "smart"
    else:
        mode = "momentum"

    smart = min(wallet_count/5,1)

    return {
        "mint": m,
        "price": price,
        "breakout": breakout,
        "smart": smart,
        "source": src,
        "mode": mode
    }

# ================= SOURCE LEARNING =================
def source_weight(src):
    s = SOURCE_STATS[src]
    total = s["wins"] + s["losses"]

    if total < 5:
        return 1.0

    winrate = s["wins"]/max(total,1)

    if winrate > 0.6:
        return 1.3
    elif winrate < 0.3:
        return 0.6

    return 1.0

# ================= SCORE（修正爆炸） =================
def score_alpha(f):
    base = (
        min(f["breakout"],0.05)*5 +
        f["smart"]*0.3
    )
    return base * source_weight(f["source"])

# ================= SIZE =================
def size(score):
    base = engine.capital * 0.1
    if score > 0.7:
        base *= 1.5
    return min(base, engine.capital * MAX_POSITION_SIZE)

# ================= SELL =================
async def check_sell(p):
    data = await get_price(p["mint"])
    if not data: return

    price,_ = data
    pnl = (price - p["entry"]) / p["entry"]

    if pnl >= TAKE_PROFIT or pnl <= STOP_LOSS:
        engine.positions.remove(p)
        engine.capital += p["size"] * (1+pnl)

        src = p["source"]
        if pnl > 0:
            SOURCE_STATS[src]["wins"] += 1
        else:
            SOURCE_STATS[src]["losses"] += 1

        log(f"SELL {p['mint'][:6]} pnl={pnl:.4f}")

# ================= EXECUTE =================
async def execute_trade(f, score):
    m = f["mint"]

    if any(p["mint"] == m for p in engine.positions):
        return False

    if time.time() - LAST_TRADE[m] < TOKEN_COOLDOWN:
        return False

    if len(engine.positions) >= MAX_POSITIONS:
        return False

    if exposure() > engine.capital * MAX_EXPOSURE:
        return False

    # 防假數據
    if f["breakout"] > 0.1:
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

    log(f"BUY {m[:6]} {f['mode']} score={score:.3f}")
    return True

# ================= LOOP =================
async def main_loop():
    ensure_engine()
    log("🚀 V36.1 TRUE FUSION FUND START")

    while engine.running:
        try:
            tokens = await fetch_candidates()

            candidates = []

            for t in tokens:
                f = await features(t)
                if not f:
                    continue

                # ⭐ adaptive filter（保留但不鎖死）
                ok = True
                if f["mode"] == "smart":
                    try:
                        ok,_ = adaptive_filter(f,None,engine.no_trade_cycles)
                    except:
                        ok = True
                else:
                    log(f"FILTER_BYPASS_MOMENTUM {f['mint'][:6]}")

                if not ok:
                    continue

                score = score_alpha(f)
                candidates.append((score,f))

            # ⭐ 只打最強
            candidates.sort(reverse=True, key=lambda x:x[0])

            for score,f in candidates[:2]:
                await execute_trade(f, score)

            for p in list(engine.positions):
                await check_sell(p)

        except Exception as e:
            log(f"ERR {e}")

        await asyncio.sleep(2)
