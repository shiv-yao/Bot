import asyncio
import time
from collections import defaultdict

from app.state import engine
from app.metrics import compute_metrics
from app.alpha.combiner import combine_scores
from app.alpha.adaptive_filter import adaptive_filter

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
MAX_EXPOSURE = 0.50
MAX_POSITION_SIZE = 0.15

TAKE_PROFIT = 0.03
STOP_LOSS = -0.015
MAX_HOLD_SEC = 30

TOKEN_COOLDOWN = 15

SOL_MINT = "So11111111111111111111111111111111111111112"
QUOTE_AMOUNT = 1_000_000

LAST_TRADE = defaultdict(float)


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

    engine.strategy_weights = getattr(engine, "strategy_weights", {
        "breakout": 0.25,
        "smart_money": 0.25,
        "liquidity": 0.20,
        "insider": 0.15,
        "fusion": 0.15,
    })

    engine.running = getattr(engine, "running", True)
    engine.regime = getattr(engine, "regime", "unknown")
    engine.last_signal = getattr(engine, "last_signal", "")
    engine.last_trade = getattr(engine, "last_trade", "")


# ===== LOG =====
def log(msg):
    print(msg)
    engine.logs.append(str(msg))
    engine.logs = engine.logs[-200:]


# ===== UTILS =====
def safe_float(x, default=0.0):
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


# ===== RISK ENGINE =====
def risk_check():
    if engine.peak_capital > 0:
        dd = (engine.capital - engine.peak_capital) / engine.peak_capital
    else:
        dd = 0.0

    if dd < -0.25:
        log("🛑 HARD STOP DD")
        engine.running = False
        return False

    return True


def exposure():
    return sum(safe_float(p.get("size", 0.0), 0.0) for p in engine.positions)


# ===== ALPHA =====
async def build_features(mint):
    try:
        wallets = await update_token_wallets(mint)
    except Exception:
        wallets = []

    try:
        quote = await get_quote(SOL_MINT, mint, QUOTE_AMOUNT)
    except Exception:
        quote = None

    if not quote:
        return None

    out_amount = safe_float(quote.get("outAmount", 0.0), 0.0)
    price_impact = safe_float(quote.get("priceImpactPct", 1.0), 1.0)

    if out_amount <= 0:
        return None

    liquidity = out_amount / 1e6

    return {
        "breakout": 0.02,
        "smart_money": min(len(wallets) / 10, 1.0),
        "liquidity": liquidity,
        "insider": 0.05,
        "wallet_count": len(wallets),
        "price_impact": price_impact,
        "out_amount": out_amount,
    }


# ===== POSITION SIZE =====
def get_size(score):
    size = engine.capital * 0.10

    if score > 0.70:
        size *= 1.5

    size = min(size, engine.capital * MAX_POSITION_SIZE)
    size = max(size, 0.02)

    return size


# ===== SELL =====
async def try_sell(pos):
    # 先保留簡化版 exit，不讓系統卡死
    price = pos["entry"] * 1.02
    pnl = (price - pos["entry"]) / pos["entry"]
    held = time.time() - pos["time"]

    log(f"CHECK_EXIT {pos['mint'][:6]} pnl={pnl:.4f} held={held:.1f}")

    if pnl >= TAKE_PROFIT or pnl <= STOP_LOSS or held > MAX_HOLD_SEC:
        engine.positions.remove(pos)

        engine.capital += pos["size"] * (1 + pnl)

        engine.trade_history.append({
            "mint": pos["mint"],
            "pnl": pnl,
            "reason": "TP" if pnl >= TAKE_PROFIT else ("SL" if pnl <= STOP_LOSS else "TIME"),
            "score": pos.get("score", 0.0),
            "size": pos.get("size", 0.0),
            "timestamp": time.time(),
            "meta": pos.get("meta", {}),
        })

        if pnl >= 0:
            engine.stats["wins"] += 1
        else:
            engine.stats["losses"] += 1

        if engine.capital > engine.peak_capital:
            engine.peak_capital = engine.capital

        engine.last_trade = f"{pos['mint'][:6]} pnl={pnl:.4f}"
        log(f"SELL {pos['mint'][:6]} pnl={pnl:.4f}")


# ===== TRADE =====
async def try_trade(mint):
    now = time.time()

    if now - LAST_TRADE[mint] < TOKEN_COOLDOWN:
        return

    if len(engine.positions) >= MAX_POSITIONS:
        engine.stats["rejected"] += 1
        log("MAX_POSITIONS")
        return

    if exposure() > engine.capital * MAX_EXPOSURE:
        engine.stats["rejected"] += 1
        log("EXPOSURE_LIMIT")
        return

    f = await build_features(mint)

    if not f:
        engine.stats["rejected"] += 1
        log(f"NO_FEATURE {mint[:6]}")
        return

    metrics = None
    if len(engine.trade_history) > 5:
        try:
            metrics = compute_metrics(engine)
        except Exception as e:
            log(f"METRICS_ERR {e}")
            metrics = None

    try:
        ok, th = adaptive_filter(f, metrics)
    except Exception as e:
        engine.stats["errors"] += 1
        log(f"FILTER_ERR {e}")
        return

    state = th.get("state", "unknown")

    if not ok:
        engine.stats["rejected"] += 1
        log(
            f"SKIP {mint[:6]} "
            f"state={state} "
            f"w={f.get('wallet_count', 0)} "
            f"liq={f.get('liquidity', 0):.4f} "
            f"imp={f.get('price_impact', 0):.4f}"
        )
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

    score *= th.get("score_boost", 1.0)

    if f["wallet_count"] <= 2:
        score *= 0.75

    if score < 0.25:
        engine.stats["rejected"] += 1
        log(f"LOW_SCORE {mint[:6]} state={state} score={score:.4f}")
        return

    size = get_size(score)

    if engine.capital < size:
        engine.stats["rejected"] += 1
        log("NO_CAPITAL")
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
            "source": "fusion",
            "filter_state": state,
        },
    })

    LAST_TRADE[mint] = now
    engine.stats["signals"] += 1
    engine.stats["executed"] += 1
    engine.last_signal = f"{mint[:6]} state={state} score={score:.4f}"

    log(
        f"BUY {mint[:6]} "
        f"state={state} "
        f"size={size:.4f} "
        f"score={score:.3f}"
    )


# ===== MAIN LOOP =====
async def main_loop():
    ensure_engine()
    log("🔥 V24 AI FILTER ENGINE START")

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
                log(
                    f"📊 WR={m['performance']['win_rate']} "
                    f"PF={m['performance']['profit_factor']} "
                    f"DD={m['performance']['max_drawdown']}"
                )

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"ERR {e}")

        await asyncio.sleep(2)
