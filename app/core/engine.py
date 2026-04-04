# ================= V37 TRUE TRADING (FULL FUSION UPGRADE) =================

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
    async def get_quote(a, b, c): return None
    def looks_like_solana_mint(x): return True

try:
    from app.alpha.helius_wallet_tracker import update_token_wallets
except:
    async def update_token_wallets(m): return []

# ================= CONFIG =================
MAX_POSITIONS = 3
MAX_EXPOSURE = 0.5
MAX_POSITION_SIZE = 0.25

TAKE_PROFIT = 0.05
STOP_LOSS = -0.02
TRAILING_GAP = 0.012
MAX_HOLD_SEC = 60

TOKEN_COOLDOWN = 10

SOL = "So11111111111111111111111111111111111111112"
AMOUNT = 1_000_000

LAST_TRADE = defaultdict(float)
LAST_PRICE = {}

# ================= FUND MEMORY =================
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
            q = await get_quote(i, o, a)
            if q and q.get("outAmount"):
                return q
        except:
            pass
        await asyncio.sleep(0.2)
    return None

async def get_price(m):
    q = await safe_quote(SOL, m, AMOUNT)
    if not q:
        return None

    out = sf(q.get("outAmount", 0))
    if out <= 0 or out > 1e12:
        return None

    price = out / 1e6
    if price <= 0 or price > 1000:
        return None

    return price, q

# ================= FEATURES =================
async def features(t):
    m = t["mint"]
    src = t.get("source", "unknown")

    wallets = await update_token_wallets(m)
    data = await get_price(m)
    if not data:
        return None

    price, q = data
    prev = LAST_PRICE.get(m)

    breakout = 0.0
    if prev:
        breakout = max((price - prev) / prev, 0)
    else:
        breakout = 0.008   # 🔥 V37：放寬初始進場

    LAST_PRICE[m] = price

    liq = sf(q.get("outAmount", 0)) / 1e5
    smart = min(len(wallets) / 5, 1)

    # 🔥 V37：放寬條件（原本太嚴）
    if breakout < 0.002:
        log(f"FEATURE_BREAKOUT_FAIL {m[:6]} {breakout:.4f}")
        return None

    if liq < 0.001:
        log(f"FEATURE_LIQ_FAIL {m[:6]} {liq:.4f}")
        return None

    return {
        "mint": m,
        "source": src,
        "breakout": breakout,
        "smart_money": smart,
        "liquidity": liq,
        "price": price,
        "is_new": prev is None,
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

# ================= STRATEGY =================
def detect_mode(f):
    if f["is_new"]:
        return "sniper"
    if f["smart_money"] > 0.6:
        return "smart"
    return "momentum"

# ================= SCORE =================
def score_alpha(f):

    mode = detect_mode(f)

    if mode == "sniper":
        base = (
            f["breakout"] * 0.4 +
            f["liquidity"] * 0.3 +
            f["smart_money"] * 0.3
        )

    elif mode == "smart":
        base = (
            f["smart_money"] * 0.5 +
            f["breakout"] * 0.3 +
            f["liquidity"] * 0.2
        )

    else:
        base = (
            f["breakout"] * 0.6 +
            f["liquidity"] * 0.3 +
            f["smart_money"] * 0.1
        )

    return base * source_weight(f["source"]), mode

# ================= SIZE =================
def size(score):
    base = engine.capital * 0.05

    if score > 0.01:
        base *= 1.5

    return min(base, engine.capital * MAX_POSITION_SIZE)

# ================= SELL =================
async def check_sell(p):
    data = await get_price(p["mint"])
    if not data:
        return

    price, _ = data
    pnl = (price - p["entry"]) / p["entry"]
    pnl = max(min(pnl, 1.0), -1.0)

    reason = None

    if pnl >= TAKE_PROFIT:
        reason = "TP"
    elif pnl <= STOP_LOSS:
        reason = "SL"

    if not reason:
        return

    engine.positions.remove(p)
    engine.capital += p["size"] * (1 + pnl)

    src = p["source"]

    if pnl > 0:
        SOURCE_STATS[src]["wins"] += 1
    else:
        SOURCE_STATS[src]["losses"] += 1

    SOURCE_STATS[src]["pnl"] += pnl

    log(f"SELL {p['mint'][:6]} {reason} pnl={pnl:.4f}")

# ================= TRADE =================
async def trade(t):

    if exposure() > engine.capital * MAX_EXPOSURE:
        return False

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

    ok, _ = adaptive_filter(f, None, engine.no_trade_cycles)
    if not ok:
        return False

    score, mode = score_alpha(f)

    # 🔥 V37：降低門檻（核心）
    threshold = 0.003

    # 🔥 fallback（保證會下單）
    if score < threshold:
        if mode == "sniper" and score > 0.001:
            pass
        else:
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

    log(f"BUY {m[:6]} {mode} score={score:.4f}")

    return True

# ================= LOOP =================
async def main_loop():
    ensure_engine()
    log("🚀 V37 TRUE TRADING START")

    while engine.running:

        try:
            tokens = await fetch_candidates()

            # 🔥 V37：打亂（避免固定順序卡死）
            random.shuffle(tokens)

            traded = False

            for t in tokens[:20]:
                ok = await trade(t)
                if ok:
                    traded = True

            for p in list(engine.positions):
                await check_sell(p)

            if not traded:
                engine.no_trade_cycles += 1
            else:
                engine.no_trade_cycles = 0

            # 🔥 防爆
            if engine.capital > 100:
                log("⚠️ CAPITAL RESET")
                engine.capital = 5.0

        except Exception as e:
            log(f"ERR {e}")

        await asyncio.sleep(2)
