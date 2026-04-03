# ================= V26.2 PROFIT FIX =================
import asyncio
import time
from collections import defaultdict

from app.state import engine
from app.metrics import compute_metrics
from app.alpha.combiner import combine_scores
from app.alpha.adaptive_filter import adaptive_filter

# ===== SAFE IMPORT =====
try:
    from app.sources.pump import fetch_pump_candidates
except:
    async def fetch_pump_candidates():
        return []

try:
    from app.data.market import get_quote
except:
    async def get_quote(a, b, c):
        return None

try:
    from app.alpha.helius_wallet_tracker import update_token_wallets
except:
    async def update_token_wallets(m):
        return []

# ===== CONFIG（調整後）=====
MAX_POSITIONS = 3
MAX_EXPOSURE = 0.45
MAX_POSITION_SIZE = 0.18

TAKE_PROFIT = 0.05
STOP_LOSS = -0.02
TRAILING_GAP = 0.012
MAX_HOLD_SEC = 45

TOKEN_COOLDOWN = 12
FORCE_TRADE_INTERVAL = 20

SOL = "So11111111111111111111111111111111111111112"
AMOUNT = 1_000_000

LAST_TRADE = defaultdict(float)
LAST_EXEC = 0
LAST_PRICE = {}

# ===== INIT =====
def ensure_engine():
    engine.positions = getattr(engine, "positions", [])
    engine.trade_history = getattr(engine, "trade_history", [])
    engine.logs = getattr(engine, "logs", [])

    engine.stats = getattr(engine, "stats", {
        "signals": 0,
        "executed": 0,
        "wins": 0,
        "losses": 0,
        "errors": 0,
        "rejected": 0,
    })

    engine.capital = getattr(engine, "capital", 5.0)
    engine.start_capital = getattr(engine, "start_capital", engine.capital)
    engine.peak_capital = getattr(engine, "peak_capital", engine.capital)

    engine.running = getattr(engine, "running", True)
    engine.no_trade_cycles = getattr(engine, "no_trade_cycles", 0)

# ===== LOG =====
def log(msg):
    print(msg)
    engine.logs.append(str(msg))
    engine.logs = engine.logs[-200:]

def sf(x):
    try:
        return float(x)
    except:
        return 0.0

# ===== RISK =====
def risk():
    if engine.peak_capital > 0:
        dd = (engine.capital - engine.peak_capital) / engine.peak_capital
        if dd < -0.30:
            log("🛑 HARD STOP")
            engine.running = False
            return False
    return True

def exposure():
    return sum(sf(p.get("size", 0)) for p in engine.positions)

# ===== PRICE =====
async def get_price(m):
    q = await get_quote(SOL, m, AMOUNT)
    if not q or not q.get("outAmount"):
        return None
    out = sf(q["outAmount"])
    if out <= 0:
        return None
    return out / 1e6, q

# ===== FEATURES（V26.2 核心升級）=====
async def features(m):
    try:
        wallets = await update_token_wallets(m)
    except:
        wallets = []

    data = await get_price(m)
    if not data:
        return None

    price, q = data

    prev = LAST_PRICE.get(m)
    breakout = 0.0
    if prev:
        breakout = max((price - prev) / prev, 0)

    LAST_PRICE[m] = price

    liq = sf(q.get("outAmount", 0)) / 1e5
    impact = sf(q.get("priceImpactPct", 1))

    # 🚨 NEW：砍掉垃圾幣
    if breakout < 0.01:
        return None
    if liq < 0.0015:
        return None
    if len(wallets) < 2:
        return None

    return {
        "breakout": breakout,
        "smart_money": min(len(wallets)/8, 1),
        "liquidity": liq,
        "insider": 0.05,
        "wallet_count": len(wallets),
        "price": price,
        "impact": impact,
    }

# ===== SIZE =====
def size(score):
    base = engine.capital * 0.08
    if score > 0.4:
        base *= 1.3
    return min(base, engine.capital * MAX_POSITION_SIZE)

# ===== EXIT =====
async def check_sell(p):
    data = await get_price(p["mint"])
    if not data:
        return
    price, _ = data
    entry = p["entry"]
    pnl = (price - entry) / entry

    held = time.time() - p["time"]

    reason = None
    if pnl >= TAKE_PROFIT:
        reason = "TP"
    elif pnl <= STOP_LOSS:
        reason = "SL"
    elif pnl < p.get("peak", 0) - TRAILING_GAP:
        reason = "TRAIL"
    elif held > MAX_HOLD_SEC and pnl < 0.01:  # 🔥 修正
        reason = "TIME"

    if pnl > p.get("peak", 0):
        p["peak"] = pnl

    if not reason:
        return

    engine.positions.remove(p)
    engine.capital += p["size"] * (1 + pnl)

    engine.trade_history.append({
        "mint": p["mint"],
        "pnl": pnl,
        "reason": reason,
        "timestamp": time.time(),
    })

    if pnl > 0:
        engine.stats["wins"] += 1
    else:
        engine.stats["losses"] += 1

    if engine.capital > engine.peak_capital:
        engine.peak_capital = engine.capital

    log(f"SELL {p['mint'][:6]} {reason} pnl={pnl:.4f}")

# ===== TRADE（V26.2 核心）=====
async def trade(m):
    global LAST_EXEC

    now = time.time()

    if now - LAST_TRADE[m] < TOKEN_COOLDOWN:
        return False

    if len(engine.positions) >= MAX_POSITIONS:
        return False

    if exposure() > engine.capital * MAX_EXPOSURE:
        return False

    f = await features(m)
    if not f:
        return False

    ok, th = adaptive_filter(f, None, engine.no_trade_cycles)

    # 🚨 V26.2：不亂 FORCE
    if not ok:
        return False

    score = combine_scores(
        f["breakout"],
        f["smart_money"],
        f["liquidity"],
        f["insider"],
        "unknown",
        {},
        {},
    )

    # 🚨 提高門檻
    if score < 0.25:
        return False

    s = size(score)
    if engine.capital < s:
        return False

    engine.capital -= s

    engine.positions.append({
        "mint": m,
        "entry": f["price"],
        "size": s,
        "time": now,
        "peak": 0.0,
    })

    LAST_TRADE[m] = now
    LAST_EXEC = now

    engine.stats["signals"] += 1
    engine.stats["executed"] += 1

    log(f"BUY {m[:6]} score={score:.3f}")

    return True

# ===== MAIN =====
async def main_loop():
    ensure_engine()
    log("🔥 V26.2 PROFIT MODE START")

    while engine.running:
        traded = False

        try:
            if not risk():
                break

            tokens = await fetch_pump_candidates()

            for t in tokens:
                m = t.get("mint")
                if m:
                    if await trade(m):
                        traded = True

            for p in list(engine.positions):
                await check_sell(p)

            if not traded:
                engine.no_trade_cycles += 1
            else:
                engine.no_trade_cycles = 0

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"ERR {e}")

        await asyncio.sleep(2)
