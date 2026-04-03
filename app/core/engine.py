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
LAST_EXECUTION = 0
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

    engine.running = True
    engine.no_trade_cycles = getattr(engine, "no_trade_cycles", 0)


def log(msg):
    print(msg)
    engine.logs.append(str(msg))
    engine.logs = engine.logs[-200:]


def safe(x, d=0):
    try:
        return float(x)
    except:
        return d


# ===== PRICE =====
async def get_price(mint):
    try:
        q = await get_quote(SOL, mint, AMOUNT)
        if not q or not q.get("outAmount"):
            return None, None

        out = safe(q["outAmount"])
        if out <= 0:
            return None, None

        return out / 1e6, q
    except:
        return None, None


# ===== FEATURES =====
async def build_features(mint):
    wallets = await update_token_wallets(mint)
    price_data = await get_price(mint)

    if not price_data:
        return None

    price, q = price_data

    prev = LAST_PRICE.get(mint)
    breakout = 0.02
    if prev:
        breakout = max((price - prev) / prev, 0)

    LAST_PRICE[mint] = price

    liq = safe(q.get("outAmount")) / 1e5

    return {
        "breakout": breakout,
        "smart_money": min(len(wallets)/8, 1),
        "liquidity": liq,
        "insider": 0.05,
        "wallet_count": len(wallets),
        "price_impact": safe(q.get("priceImpactPct", 1)),
        "price": price,
    }


# ===== SIZE =====
def get_size(score):
    base = engine.capital * 0.08

    if score > 0.6:
        base *= 1.5
    elif score > 0.4:
        base *= 1.2

    base = min(base, engine.capital * MAX_POSITION_SIZE)
    return max(base, 0.02)


# ===== SELL FIX（關鍵）=====
async def mark_to_market(pos):
    price, _ = await get_price(pos["mint"])

    if price is None:
        return 0.0, pos["entry"]

    entry = safe(pos["entry"])
    if entry <= 0:
        return 0.0, price

    pnl = (price - entry) / entry
    return pnl, price


def trailing_hit(pos, pnl):
    peak = pos.get("peak_pnl", pnl)
    if pnl > peak:
        pos["peak_pnl"] = pnl
        peak = pnl

    return pnl < peak - TRAILING_GAP


async def try_sell(pos):
    pnl, _ = await mark_to_market(pos)
    held = time.time() - pos["time"]

    reason = None

    if pnl >= TAKE_PROFIT:
        reason = "TP"
    elif pnl <= STOP_LOSS:
        reason = "SL"
    elif trailing_hit(pos, pnl):
        reason = "TRAIL"
    elif held > MAX_HOLD_SEC:
        reason = "TIME"
    elif pnl == 0 and held > 20:
        reason = "FORCE_EXIT"   # 🔥 防卡死

    log(f"CHECK_EXIT {pos['mint'][:6]} pnl={pnl:.4f}")

    if not reason:
        return

    engine.positions.remove(pos)
    engine.capital += pos["size"] * (1 + pnl)

    engine.trade_history.append({
        "mint": pos["mint"],
        "pnl": pnl,
        "reason": reason,
        "meta": pos["meta"],
    })

    if pnl >= 0:
        engine.stats["wins"] += 1
    else:
        engine.stats["losses"] += 1

    log(f"SELL {pos['mint'][:6]} {reason} pnl={pnl:.4f}")


# ===== TRADE =====
async def try_trade(mint):
    global LAST_EXECUTION

    now = time.time()

    if now - LAST_TRADE[mint] < TOKEN_COOLDOWN:
        return False

    if len(engine.positions) >= MAX_POSITIONS:
        return False

    f = await build_features(mint)
    if not f:
        return False

    metrics = None
    if len(engine.trade_history) > 5:
        metrics = compute_metrics(engine)

    ok, th = adaptive_filter(f, metrics, engine.no_trade_cycles)

    loosen = now - LAST_EXECUTION > FORCE_TRADE_INTERVAL

    if not ok and not loosen:
        return False

    score = combine_scores(
        f["breakout"],
        f["smart_money"],
        f["liquidity"],
        f["insider"],
        "unknown", {}, {}
    )

    if score < th["score_min"] and not loosen:
        return False

    size = get_size(score)

    engine.capital -= size

    engine.positions.append({
        "mint": mint,
        "entry": f["price"],
        "size": size,
        "score": score,
        "time": now,
        "peak_pnl": 0,
        "meta": f,
    })

    LAST_TRADE[mint] = now
    LAST_EXECUTION = now

    engine.stats["signals"] += 1
    engine.stats["executed"] += 1

    log(f"BUY {mint[:6]} score={score:.3f}")

    return True


# ===== MAIN LOOP =====
async def main_loop():
    ensure_engine()
    log("🔥 V26.1 FINAL START")

    while engine.running:
        traded = False

        try:
            tokens = await fetch_pump_candidates()

            for t in tokens:
                mint = t.get("mint")
                if mint:
                    if await try_trade(mint):
                        traded = True

            for p in list(engine.positions):
                await try_sell(p)

            if traded:
                engine.no_trade_cycles = 0
            else:
                engine.no_trade_cycles += 1

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"ERR {e}")

        await asyncio.sleep(2)
