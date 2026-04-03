import asyncio
import time
from collections import defaultdict
from statistics import mean

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
    async def update_token_wallets(mint):
        return []


# ===== CONFIG =====
MAX_POSITIONS = 4
MAX_EXPOSURE = 0.5
MAX_POSITION_SIZE = 0.2

TAKE_PROFIT = 0.04
STOP_LOSS = -0.02
TRAILING_GAP = 0.015
MAX_HOLD_SEC = 40

TOKEN_COOLDOWN = 10
FORCE_TRADE_INTERVAL = 15

SOL = "So11111111111111111111111111111111111111112"
AMOUNT = 1_000_000

LAST_TRADE = defaultdict(float)
LAST_EXEC = 0
LAST_PRICE = {}

# ⭐ NEW（學習用）
SCORE_HISTORY = []
FORCED_HISTORY = []


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

    engine.running = True
    engine.no_trade_cycles = getattr(engine, "no_trade_cycles", 0)


def log(x):
    print(x)
    engine.logs.append(str(x))
    engine.logs = engine.logs[-200:]


# ===== PRICE =====
async def get_price(mint):
    q = await get_quote(SOL, mint, AMOUNT)
    if not q or not q.get("outAmount"):
        return None
    return float(q["outAmount"]) / 1e6, q


# ===== FEATURES =====
async def build_features(mint):
    wallets = await update_token_wallets(mint)
    p = await get_price(mint)
    if not p:
        return None

    price, q = p
    prev = LAST_PRICE.get(mint)

    breakout = 0.02
    if prev:
        breakout = max((price - prev) / prev, 0)

    LAST_PRICE[mint] = price

    return {
        "breakout": breakout,
        "smart_money": min(len(wallets)/8, 1),
        "liquidity": float(q["outAmount"]) / 1e5,
        "insider": 0.05,
        "wallet_count": len(wallets),
        "price": price,
        "price_impact": float(q.get("priceImpactPct", 0))
    }


# ===== SCORE NORMALIZATION ⭐核心 =====
def normalize_score(raw_score):
    SCORE_HISTORY.append(raw_score)
    if len(SCORE_HISTORY) > 100:
        SCORE_HISTORY.pop(0)

    avg = mean(SCORE_HISTORY)
    if avg == 0:
        return raw_score * 6

    # ⭐ 自動 scaling
    scale = 0.2 / avg
    return raw_score * scale


# ===== DYNAMIC THRESHOLD =====
def dynamic_score_min():
    if len(SCORE_HISTORY) < 10:
        return 0.18

    avg = mean(SCORE_HISTORY)
    return max(0.12, avg * 0.8)


# ===== SIZE =====
def get_size(score):
    base = engine.capital * 0.08
    if score > 0.6:
        base *= 1.5
    return min(base, engine.capital * MAX_POSITION_SIZE)


# ===== SELL =====
async def try_sell(p):
    price_data = await get_price(p["mint"])
    if not price_data:
        return

    price, _ = price_data
    pnl = (price - p["entry"]) / p["entry"]

    if pnl >= TAKE_PROFIT or pnl <= STOP_LOSS:
        engine.positions.remove(p)
        engine.capital += p["size"] * (1 + pnl)

        engine.trade_history.append({
            "mint": p["mint"],
            "pnl": pnl,
            "meta": p["meta"]
        })

        if p["meta"].get("forced"):
            FORCED_HISTORY.append(pnl)

        if pnl > 0:
            engine.stats["wins"] += 1
        else:
            engine.stats["losses"] += 1

        log(f"SELL {p['mint'][:6]} pnl={pnl:.4f}")


# ===== TRADE =====
async def try_trade(mint):
    global LAST_EXEC

    now = time.time()

    if now - LAST_TRADE[mint] < TOKEN_COOLDOWN:
        return

    f = await build_features(mint)
    if not f:
        return

    ok, th = adaptive_filter(
        f,
        compute_metrics(engine),
        engine.no_trade_cycles
    )

    raw_score = combine_scores(
        f["breakout"],
        f["smart_money"],
        f["liquidity"],
        f["insider"],
        "unknown",
        {},
        {}
    )

    score = normalize_score(raw_score)

    score_min = dynamic_score_min()

    # ⭐ forced trade 自動學習
    force_allowed = True
    if len(FORCED_HISTORY) > 10:
        avg = mean(FORCED_HISTORY[-10:])
        if avg < -0.01:
            force_allowed = False

    if not ok and force_allowed:
        if f["wallet_count"] >= 1 and f["liquidity"] > 0.003:
            forced = True
        else:
            return
    else:
        forced = False

    if score < score_min and not forced:
        return

    size = get_size(score)
    if engine.capital < size:
        return

    engine.capital -= size

    engine.positions.append({
        "mint": mint,
        "entry": f["price"],
        "size": size,
        "score": score,
        "time": now,
        "meta": {
            **f,
            "forced": forced
        }
    })

    LAST_TRADE[mint] = now
    LAST_EXEC = now

    log(f"BUY {mint[:6]} score={score:.3f} forced={forced}")


# ===== MAIN =====
async def main_loop():
    ensure_engine()
    log("🔥 V25.2 FINAL START")

    while engine.running:
        traded = False

        try:
            tokens = await fetch_pump_candidates()

            for t in tokens:
                if await try_trade(t["mint"]):
                    traded = True

            for p in list(engine.positions):
                await try_sell(p)

            if traded:
                engine.no_trade_cycles = 0
            else:
                engine.no_trade_cycles += 1

        except Exception as e:
            log(f"ERR {e}")

        await asyncio.sleep(2)
