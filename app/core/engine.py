import asyncio
import time
import random
from collections import defaultdict

from app.state import engine
from app.alpha.combiner import combine_scores
from app.alpha.adaptive_filter import adaptive_filter

try:
    from app.sources.fusion import fetch_candidates
except Exception:
    async def fetch_candidates():
        return []

try:
    from app.data.market import get_quote, looks_like_solana_mint
except Exception:
    async def get_quote(a, b, c):
        return None

    def looks_like_solana_mint(x):
        return True

try:
    from app.alpha.helius_wallet_tracker import update_token_wallets
except Exception:
    async def update_token_wallets(m):
        return []


# ===== 🔥 風控強化 =====
MAX_POSITIONS = 1
MAX_EXPOSURE = 0.25
MAX_POSITION_SIZE = 0.08

TAKE_PROFIT = 0.04
STOP_LOSS = -0.01
TRAILING_GAP = 0.008
MAX_HOLD_SEC = 25

TOKEN_COOLDOWN = 15

SOL = "So11111111111111111111111111111111111111112"
AMOUNT = 1_000_000

LAST_TRADE = defaultdict(float)
LAST_PRICE = {}


def ensure_engine():
    engine.positions = getattr(engine, "positions", [])
    engine.trade_history = getattr(engine, "trade_history", [])
    engine.logs = getattr(engine, "logs", [])

    engine.stats = getattr(
        engine,
        "stats",
        {
            "signals": 0,
            "executed": 0,
            "wins": 0,
            "losses": 0,
            "errors": 0,
            "rejected": 0,
        },
    )

    engine.capital = getattr(engine, "capital", 5.0)
    engine.start_capital = getattr(engine, "start_capital", engine.capital)
    engine.peak_capital = getattr(engine, "peak_capital", engine.capital)

    engine.running = getattr(engine, "running", True)
    engine.no_trade_cycles = getattr(engine, "no_trade_cycles", 0)
    engine.last_signal = getattr(engine, "last_signal", "")
    engine.last_trade = getattr(engine, "last_trade", "")


def log(msg):
    print(msg)
    engine.logs.append(str(msg))
    engine.logs = engine.logs[-300:]


def sf(x):
    try:
        return float(x)
    except:
        return 0.0


# ===== 🔥 防爆倉（新增） =====
def risk():
    if engine.peak_capital > 0:
        dd = (engine.capital - engine.peak_capital) / engine.peak_capital

        # 🔴 硬停
        if dd < -0.30:
            log("🛑 HARD STOP")
            engine.running = False
            return False

        # 🟡 降風險模式
        if dd < -0.15:
            engine.no_trade_cycles += 2
            log("⚠️ RISK MODE ACTIVE")

    return True


def exposure():
    return sum(sf(p.get("size", 0)) for p in engine.positions)


async def safe_quote(input_mint, output_mint, amount):
    for _ in range(3):
        try:
            q = await get_quote(input_mint, output_mint, amount)
            if q and q.get("outAmount"):
                return q
        except Exception as e:
            log(f"QUOTE_ERR {str(e)[:60]}")
        await asyncio.sleep(0.25 + random.random() * 0.35)
    return None


async def get_price(mint):
    if not looks_like_solana_mint(mint):
        return None

    q = await safe_quote(SOL, mint, AMOUNT)
    if not q:
        return None

    out = sf(q.get("outAmount", 0))
    if out <= 0:
        return None

    return out / 1e6, q


# ===== 🔥 特徵優化 =====
async def features(t):
    mint = t["mint"]
    source = t.get("source", "unknown")

    try:
        wallets = await update_token_wallets(mint)
    except:
        wallets = []

    data = await get_price(mint)
    if not data:
        return None

    price, q = data
    prev = LAST_PRICE.get(mint)

    breakout = 0.0
    if prev:
        breakout = max((price - prev) / prev, 0)

    liq = sf(q.get("outAmount", 0)) / 1e5

    # ===== 🔥 初始 momentum 注入 =====
    if prev is None:
        breakout = 0.008

    LAST_PRICE[mint] = price

    # ===== 🔥 過濾 =====
    if breakout < 0.004:
        return None

    if liq < 0.002:
        return None

    # 🔥 wallet 不再硬卡
    smart_money = min(len(wallets) / 5, 1)

    return {
        "mint": mint,
        "source": source,
        "breakout": breakout,
        "smart_money": smart_money,
        "liquidity": liq,
        "insider": 0.02,
        "price": price,
    }


def size(score):
    base = engine.capital * 0.05
    if score > 0.35:
        base *= 1.2
    return min(base, engine.capital * MAX_POSITION_SIZE)


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
    elif held > MAX_HOLD_SEC:
        reason = "TIME"

    if pnl > p.get("peak", 0):
        p["peak"] = pnl

    if not reason:
        return

    engine.positions.remove(p)
    engine.capital += p["size"] * (1 + pnl)

    if pnl > 0:
        engine.stats["wins"] += 1
    else:
        engine.stats["losses"] += 1

    engine.last_trade = f"{p['mint'][:6]} {reason} {pnl:.4f}"
    log(f"SELL {p['mint'][:6]} {reason} pnl={pnl:.4f}")


async def trade(t):
    mint = t["mint"]

    if any(p["mint"] == mint for p in engine.positions):
        return False

    now = time.time()

    if now - LAST_TRADE[mint] < TOKEN_COOLDOWN:
        return False

    if len(engine.positions) >= MAX_POSITIONS:
        return False

    if exposure() > engine.capital * MAX_EXPOSURE:
        return False

    f = await features(t)
    if not f:
        return False

    # ===== 🔥 FILTER 放寬 =====
    ok = True
    if engine.stats["executed"] >= 5:
        try:
            ok, _ = adaptive_filter(f, None, engine.no_trade_cycles)
        except:
            ok = True

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

    if score < 0.18:
        return False

    s = size(score)
    if engine.capital < s:
        return False

    engine.capital -= s

    engine.positions.append({
        "mint": mint,
        "entry": f["price"],
        "size": s,
        "time": now,
        "peak": 0,
    })

    LAST_TRADE[mint] = now
    engine.stats["signals"] += 1
    engine.stats["executed"] += 1

    log(f"BUY {mint[:6]} score={score:.3f}")
    return True


async def main_loop():
    ensure_engine()
    log("🔥 V29.6 RISK CONTROL START")

    while engine.running:
        traded = False

        try:
            if not risk():
                break

            tokens = await fetch_candidates()

            for t in tokens:
                if await trade(t):
                    traded = True

            for p in list(engine.positions):
                await check_sell(p)

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"ERR {e}")

        await asyncio.sleep(2)
