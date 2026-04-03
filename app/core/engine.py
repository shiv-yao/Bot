# ================= V33.4 FULL FUSION FINAL =================

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

TAKE_PROFIT = 0.08
STOP_LOSS = -0.03
HARD_STOP = -0.15
TRAILING_GAP = 0.015
MAX_HOLD_SEC = 60

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
    engine.start_capital = getattr(engine, "start_capital", engine.capital)
    engine.peak_capital = getattr(engine, "peak_capital", engine.capital)

    engine.running = getattr(engine, "running", True)
    engine.no_trade_cycles = getattr(engine, "no_trade_cycles", 0)

    engine.stats = getattr(engine, "stats", {
        "signals": 0,
        "executed": 0,
        "wins": 0,
        "losses": 0,
        "errors": 0,
    })


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
    return sum(sf(p.get("size", 0)) for p in engine.positions)


def risk():
    if engine.peak_capital > 0:
        dd = (engine.capital - engine.peak_capital) / engine.peak_capital
        if dd < -0.35:
            log("🛑 ENGINE HARD STOP")
            engine.running = False
            return False
    return True


# ================= PRICE =================
async def safe_quote(i, o, a):
    for _ in range(3):
        try:
            q = await get_quote(i, o, a)
            if q and q.get("outAmount"):
                return q
        except Exception as e:
            log(f"QUOTE_ERR {str(e)[:60]}")
        await asyncio.sleep(0.2)
    return None


async def get_price(m):
    if not looks_like_solana_mint(m):
        return None

    q = await safe_quote(SOL, m, AMOUNT)
    if not q:
        return None

    out = sf(q.get("outAmount", 0))
    if out <= 0:
        return None

    return out / 1e6, q


# ================= FEATURES =================
async def features(t):
    m = t["mint"]
    src = t.get("source", "unknown")

    try:
        wallets = await update_token_wallets(m)
    except:
        wallets = []

    data = await get_price(m)
    if not data:
        return None

    price, q = data
    prev = LAST_PRICE.get(m)

    # ===== 防垃圾價格 =====
    if price < 1e-8:
        return None

    # ===== breakout =====
    if prev:
        raw = (price - prev) / prev
        breakout = min(max(raw * 4, 0), 1)
    else:
        breakout = 0.01

    # ===== 防假跳動 =====
    if prev:
        impact = abs(price - prev) / prev
        if impact > 0.5:
            LAST_PRICE[m] = price
            return None

    LAST_PRICE[m] = price

    # ===== liquidity =====
    liq_raw = sf(q.get("outAmount", 0))
    liquidity = min(liq_raw / 1e6, 1)

    # 🔥 Dex fallback 防假幣
    if q.get("source") == "dexscreener":
        if sf(q.get("liquidityUsd", 0)) < 20000:
            return None

    # ===== smart money =====
    smart = min(len(wallets) / 5, 1)

    if breakout < 0.01:
        return None

    if liquidity < 0.002:
        return None

    return {
        "mint": m,
        "source": src,
        "breakout": breakout,
        "smart_money": smart,
        "liquidity": liquidity,
        "price": price,
    }


# ================= FUND BRAIN =================
def source_weight(src):
    s = SOURCE_STATS[src]
    total = s["wins"] + s["losses"]

    if total < 3:
        return 1.0

    winrate = s["wins"] / total

    if winrate > 0.6:
        return 1.5
    elif winrate < 0.4:
        return 0.5

    return 1.0


# ================= SCORE =================
def score_alpha(f):
    base = (
        f["breakout"] * 0.5 +
        f["smart_money"] * 0.3 +
        f["liquidity"] * 0.2
    )
    return min(base * source_weight(f["source"]), 1.0)


# ================= SIZE =================
def size(score):
    base = engine.capital * 0.08
    if score > 0.6:
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
    elif pnl <= HARD_STOP:
        reason = "HARD_SL"
    elif pnl <= STOP_LOSS:
        reason = "SL"
    elif pnl < p.get("peak",0) - TRAILING_GAP:
        reason = "TRAIL"
    elif time.time() - p["time"] > MAX_HOLD_SEC:
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
        engine.stats["wins"] += 1
    else:
        SOURCE_STATS[src]["losses"] += 1
        engine.stats["losses"] += 1

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

    ok,_ = adaptive_filter(f,None,engine.no_trade_cycles)

    # 🔥 防卡死
    if not ok and engine.no_trade_cycles < 10:
        ok = True

    if not ok:
        return False

    score = score_alpha(f)

    # 🔥 關鍵：提高門檻（止血）
    if score < 0.4:
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
        "peak": 0,
        "source": f["source"]
    })

    LAST_TRADE[m] = time.time()

    engine.stats["signals"] += 1
    engine.stats["executed"] += 1

    log(f"BUY {m[:6]} score={score:.3f}")

    return True


# ================= LOOP =================
async def main_loop():
    ensure_engine()
    log("🔥 V33.4 FULL FUSION START")

    while engine.running:

        try:
            if not risk():
                break

            tokens = await fetch_candidates()

            if not tokens:
                engine.no_trade_cycles += 1
                await asyncio.sleep(5)
                continue

            traded = False

            for t in tokens:
                if await trade(t):
                    traded = True

            for p in list(engine.positions):
                await check_sell(p)

            if traded:
                engine.no_trade_cycles = 0
            else:
                engine.no_trade_cycles += 1

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"ERR {e}")

        await asyncio.sleep(2)
