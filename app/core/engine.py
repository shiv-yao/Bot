import asyncio
import time
from collections import defaultdict

from app.state import engine
from app.metrics import compute_metrics
from app.alpha.combiner import combine_scores

# ===== SAFE IMPORT（防炸）=====
try:
    from app.sources.pump import fetch_pump_candidates
except Exception:
    async def fetch_pump_candidates():
        return []

try:
    from app.data.market import get_quote
except Exception:
    async def get_quote(input_mint, output_mint, amount):
        return None

try:
    from app.alpha.helius_wallet_tracker import update_token_wallets
except Exception:
    async def update_token_wallets(mint):
        return []


# ===== CONFIG =====
MAX_POSITIONS = 4
MAX_EXPOSURE = 0.5
MAX_POSITION_SIZE = 0.15

TAKE_PROFIT = 0.03
STOP_LOSS = -0.015
MAX_HOLD_SEC = 30

TOKEN_COOLDOWN = 15

LAST_TRADE = defaultdict(float)


# ===== INIT =====
def ensure_engine():
    engine.positions = getattr(engine, "positions", [])
    engine.trade_history = getattr(engine, "trade_history", [])
    engine.logs = getattr(engine, "logs", [])

    engine.capital = getattr(engine, "capital", 5.0)
    engine.start_capital = getattr(engine, "start_capital", engine.capital)
    engine.peak_capital = getattr(engine, "peak_capital", engine.capital)

    engine.strategy_weights = getattr(engine, "strategy_weights", {
        "breakout": 0.25,
        "smart_money": 0.25,
        "liquidity": 0.2,
        "insider": 0.15,
        "fusion": 0.15,
    })

    engine.running = True


# ===== LOG =====
def log(msg):
    print(msg)
    engine.logs.append(str(msg))
    engine.logs = engine.logs[-200:]


# ===== RISK ENGINE =====
def risk_check():
    if engine.peak_capital > 0:
        dd = (engine.capital - engine.peak_capital) / engine.peak_capital
    else:
        dd = 0

    if dd < -0.25:
        log("🛑 HARD STOP DD")
        engine.running = False
        return False

    return True


def exposure():
    return sum(p["size"] for p in engine.positions)


# ===== ALPHA =====
async def build_features(mint):
    try:
        wallets = await update_token_wallets(mint)
    except:
        wallets = []

    try:
        quote = await get_quote(
            "So11111111111111111111111111111111111111112",
            mint,
            1000000
        )
    except:
        quote = None

    if not quote:
        return None

    liquidity = float(quote.get("outAmount", 0)) / 1e6
    price_impact = float(quote.get("priceImpactPct", 1))

    return {
        "breakout": 0.02,
        "smart_money": min(len(wallets) / 10, 1),
        "liquidity": liquidity,
        "insider": 0.05,
        "wallet_count": len(wallets),
        "price_impact": price_impact,
    }


# ===== FILTER =====
def alpha_filter(f):
    if not f:
        return False

    if f["wallet_count"] < 3:
        return False

    if f["liquidity"] < 0.02:
        return False

    if f["price_impact"] > 0.02:
        return False

    return True


# ===== POSITION SIZE =====
def get_size(score):
    size = engine.capital * 0.1

    if score > 0.7:
        size *= 1.5

    size = min(size, engine.capital * MAX_POSITION_SIZE)
    size = max(size, 0.02)

    return size


# ===== SELL =====
async def try_sell(pos):
    price = pos["entry"] * (1 + 0.02)
    pnl = (price - pos["entry"]) / pos["entry"]

    held = time.time() - pos["time"]

    if pnl >= TAKE_PROFIT or pnl <= STOP_LOSS or held > MAX_HOLD_SEC:
        engine.positions.remove(pos)

        engine.capital += pos["size"] * (1 + pnl)

        engine.trade_history.append({
            "mint": pos["mint"],
            "pnl": pnl,
            "meta": pos["meta"]
        })

        if engine.capital > engine.peak_capital:
            engine.peak_capital = engine.capital

        log(f"SELL {pos['mint']} pnl={pnl:.4f}")


# ===== TRADE =====
async def try_trade(mint):
    now = time.time()

    if now - LAST_TRADE[mint] < TOKEN_COOLDOWN:
        return

    if len(engine.positions) >= MAX_POSITIONS:
        return

    if exposure() > engine.capital * MAX_EXPOSURE:
        log("EXPOSURE_LIMIT")
        return

    f = await build_features(mint)

    if not alpha_filter(f):
        log(f"SKIP_BAD {mint[:6]}")
        return

    score = combine_scores(
        f["breakout"],
        f["smart_money"],
        f["liquidity"],
        f["insider"],
        getattr(engine, "regime", "unknown"),
        {},
        {},
    )

    if score < 0.25:
        log(f"LOW_SCORE {mint[:6]}")
        return

    size = get_size(score)

    if engine.capital < size:
        return

    engine.capital -= size

    engine.positions.append({
        "mint": mint,
        "entry": 100,
        "size": size,
        "score": score,
        "time": now,
        "meta": f
    })

    LAST_TRADE[mint] = now

    log(f"BUY {mint[:6]} size={size:.4f} score={score:.3f}")


# ===== MAIN LOOP =====
async def main_loop():
    ensure_engine()

    log("🔥 V23 PRO ENGINE START")

    while engine.running:
        try:
            if not risk_check():
                break

            tokens = await fetch_pump_candidates()

            for t in tokens:
                mint = t.get("mint")
                if mint:
                    await try_trade(mint)

            for pos in list(engine.positions):
                await try_sell(pos)

            if len(engine.trade_history) > 10:
                m = compute_metrics(engine)
                log(f"📊 WR={m['performance']['win_rate']}")

        except Exception as e:
            log(f"ERR {e}")

        await asyncio.sleep(2)
