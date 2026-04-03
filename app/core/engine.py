import asyncio
import time
from collections import defaultdict
from statistics import mean

from app.state import engine
from app.metrics import compute_metrics
from app.alpha.combiner import combine_scores
from app.alpha.adaptive_filter import adaptive_filter
from app.portfolio.portfolio_manager import portfolio


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
MAX_EXPOSURE = 0.8
MAX_POSITION_SIZE = 0.2

TAKE_PROFIT = 0.04
STOP_LOSS = -0.02

TOKEN_COOLDOWN = 10
FORCE_LIMIT = 2

SOL = "So11111111111111111111111111111111111111112"
AMOUNT = 1_000_000

LAST_TRADE = defaultdict(float)
LAST_PRICE = {}

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


# ===== STRATEGY =====
def detect_strategy(f):
    if f["breakout"] > 0.03:
        return "momentum"
    if f["smart_money"] > 0.2:
        return "smart_money"
    if f["liquidity"] > 0.02:
        return "liquidity"
    return "fusion"


# ===== SCORE NORMALIZE =====
def normalize_score(raw):
    SCORE_HISTORY.append(raw)
    if len(SCORE_HISTORY) > 100:
        SCORE_HISTORY.pop(0)

    avg = mean(SCORE_HISTORY) if SCORE_HISTORY else 0
    if avg == 0:
        return raw * 6

    return raw * (0.2 / avg)


def dynamic_score_min():
    if len(SCORE_HISTORY) < 10:
        return 0.18
    return max(0.12, mean(SCORE_HISTORY) * 0.8)


# ===== SIZE =====
def get_size(score, forced):
    base = engine.capital * 0.08

    if score > 0.6:
        base *= 1.5

    if forced:
        base *= 0.4  # ⭐ forced 降風險

    return min(base, engine.capital * MAX_POSITION_SIZE)


def exposure():
    return sum(p["size"] for p in engine.positions)


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

        trade = {
            "mint": p["mint"],
            "pnl": pnl,
            "meta": p["meta"]
        }

        engine.trade_history.append(trade)
        portfolio.record_trade(trade)

        if p["meta"].get("forced"):
            FORCED_HISTORY.append(pnl)

        if pnl > 0:
            engine.stats["wins"] += 1
        else:
            engine.stats["losses"] += 1

        log(f"SELL {p['mint'][:6]} pnl={pnl:.4f}")


# ===== TRADE =====
async def try_trade(mint):
    now = time.time()

    # ===== HARD RISK LIMIT =====
    if len(engine.positions) >= MAX_POSITIONS:
        return False

    if exposure() >= engine.capital * MAX_EXPOSURE:
        return False

    if any(p["mint"] == mint for p in engine.positions):
        return False

    if now - LAST_TRADE[mint] < TOKEN_COOLDOWN:
        return False

    f = await build_features(mint)
    if not f:
        return False

    strategy = detect_strategy(f)

    # ===== portfolio control =====
    if portfolio.get_weight(strategy) == 0:
        return False

    if portfolio.source_exposure_ratio(engine, strategy) > 0.4:
        return False

    ok, _ = adaptive_filter(
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
    score *= portfolio.get_weight(strategy)

    score_min = dynamic_score_min()

    # ===== forced 控制 =====
    forced = False

    if not ok:
        forced_count = sum(1 for p in engine.positions if p["meta"].get("forced"))

        force_allowed = True
        if len(FORCED_HISTORY) > 10:
            if mean(FORCED_HISTORY[-10:]) < -0.01:
                force_allowed = False

        if force_allowed and forced_count < FORCE_LIMIT:
            if f["wallet_count"] >= 1 and f["liquidity"] > 0.003:
                forced = True
            else:
                return False
        else:
            return False

    if score < score_min and not forced:
        return False

    size = get_size(score, forced)

    if engine.capital < size:
        return False

    engine.capital -= size

    engine.positions.append({
        "mint": mint,
        "entry": f["price"],
        "size": size,
        "score": score,
        "time": now,
        "meta": {
            **f,
            "strategy": strategy,
            "forced": forced
        }
    })

    LAST_TRADE[mint] = now

    engine.stats["signals"] += 1
    engine.stats["executed"] += 1

    log(f"BUY {mint[:6]} strat={strategy} score={score:.3f} forced={forced}")

    return True


# ===== MAIN LOOP =====
async def main_loop():
    ensure_engine()
    log("🔥 V26 FINAL ENGINE START")

    while engine.running:
        traded = False

        try:
            tokens = await fetch_pump_candidates()

            for t in tokens:
                if await try_trade(t["mint"]):
                    traded = True

            for p in list(engine.positions):
                await try_sell(p)

            portfolio.update_weights()

            if traded:
                engine.no_trade_cycles = 0
            else:
                engine.no_trade_cycles += 1

        except Exception as e:
            log(f"ERR {e}")

        await asyncio.sleep(2)
