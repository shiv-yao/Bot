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

TAKE_PROFIT = 0.03
STOP_LOSS = -0.015
MAX_HOLD_SEC = 30

TOKEN_COOLDOWN = 10
FORCE_TRADE_INTERVAL = 15  # 🔥 沒交易多久強制進場

SOL = "So11111111111111111111111111111111111111112"

LAST_TRADE = defaultdict(float)
LAST_EXECUTION = 0


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
    engine.regime = getattr(engine, "regime", "unknown")


# ===== LOG =====
def log(msg):
    print(msg)
    engine.logs.append(str(msg))
    engine.logs = engine.logs[-200:]


# ===== RISK =====
def risk_check():
    dd = 0
    if engine.peak_capital > 0:
        dd = (engine.capital - engine.peak_capital) / engine.peak_capital

    # 🔥 動態風控
    if dd < -0.3:
        log("🛑 HARD STOP DD")
        engine.running = False
        return False

    return True


def exposure():
    return sum(p["size"] for p in engine.positions)


# ===== FEATURE =====
async def build_features(mint):
    wallets = await update_token_wallets(mint)
    quote = await get_quote(SOL, mint, 1_000_000)

    if not quote:
        return None

    out_amount = float(quote.get("outAmount", 0))
    impact = float(quote.get("priceImpactPct", 1))

    if out_amount <= 0:
        return None

    # 🔥 修正 liquidity（重點）
    liquidity = out_amount / 1e5

    return {
        "breakout": 0.02,
        "smart_money": min(len(wallets) / 10, 1),
        "liquidity": liquidity,
        "insider": 0.05,
        "wallet_count": len(wallets),
        "price_impact": impact,
    }


# ===== SIZE =====
def get_size(score):
    base = engine.capital * 0.1

    if score > 0.7:
        base *= 1.5

    # 🔥 DD 控制
    if engine.peak_capital > 0:
        dd = (engine.capital - engine.peak_capital) / engine.peak_capital
        if dd < -0.1:
            base *= 0.6
        if dd < -0.2:
            base *= 0.4

    base = min(base, engine.capital * MAX_POSITION_SIZE)
    base = max(base, 0.02)

    return base


# ===== SELL =====
async def try_sell(pos):
    price = pos["entry"] * 1.02
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

        if pnl >= 0:
            engine.stats["wins"] += 1
        else:
            engine.stats["losses"] += 1

        if engine.capital > engine.peak_capital:
            engine.peak_capital = engine.capital

        log(f"SELL {pos['mint'][:6]} pnl={pnl:.4f}")


# ===== TRADE =====
async def try_trade(mint):
    global LAST_EXECUTION

    now = time.time()

    if now - LAST_TRADE[mint] < TOKEN_COOLDOWN:
        return

    if len(engine.positions) >= MAX_POSITIONS:
        return

    if exposure() > engine.capital * MAX_EXPOSURE:
        return

    f = await build_features(mint)

    if not f:
        return

    metrics = None
    if len(engine.trade_history) > 5:
        metrics = compute_metrics(engine)

    ok, th = adaptive_filter(f, metrics)
    state = th.get("state", "neutral")

    # ===== 🔥 自適應放寬 =====
    loosen = False
    if len(engine.trade_history) < 5:
        loosen = True

    if now - LAST_EXECUTION > FORCE_TRADE_INTERVAL:
        loosen = True

    if not ok and not loosen:
        log(f"SKIP {mint[:6]} state={state}")
        return

    if not ok and loosen:
        log(f"⚠️ FORCE_PASS {mint[:6]}")

    score = combine_scores(
        f["breakout"],
        f["smart_money"],
        f["liquidity"],
        f["insider"],
        engine.regime,
        {},
        {},
    )

    # 🔥 放寬 score
    min_score = 0.25 if not loosen else 0.15

    if score < min_score:
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
        "meta": {
            **f,
            "state": state
        }
    })

    LAST_TRADE[mint] = now
    LAST_EXECUTION = now

    engine.stats["executed"] += 1

    log(f"BUY {mint[:6]} size={size:.4f} score={score:.3f}")


# ===== MAIN LOOP =====
async def main_loop():
    ensure_engine()
    log("🔥 V24.2 ADAPTIVE ENGINE START")

    while engine.running:
        try:
            if not risk_check():
                break

            tokens = await fetch_pump_candidates()

            for t in tokens:
                await try_trade(t["mint"])

            for pos in list(engine.positions):
                await try_sell(pos)

            if len(engine.trade_history) > 10:
                m = compute_metrics(engine)
                log(f"📊 WR={m['performance']['win_rate']}")

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"ERR {e}")

        await asyncio.sleep(2)
