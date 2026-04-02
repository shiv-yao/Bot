import asyncio
import time
import random
from collections import defaultdict

from app.state import engine
from app.metrics import compute_metrics
from app.alpha.combiner import combine_scores
from app.portfolio.allocator import get_position_size

# ===== 可選模組（避免炸）=====
try:
    from app.mempool.sniper import mempool_sniper
except:
    mempool_sniper = None

try:
    from app.discovery.pump import pump_scanner
except:
    pump_scanner = None

# ===== CONFIG =====
MAX_POSITIONS = 4
TAKE_PROFIT = 0.02
STOP_LOSS = -0.01
TOKEN_COOLDOWN = 10

LAST_TRADE = defaultdict(float)

# ===== INIT =====
def ensure_engine():
    defaults = {
        "positions": [],
        "logs": [],
        "trade_history": [],
        "stats": {
            "signals": 0,
            "executed": 0,
            "wins": 0,
            "losses": 0,
            "errors": 0,
            "rejected": 0,
        },
        "capital": 5.0,
        "start_capital": 5.0,
        "peak_capital": 5.0,
        "running": True,
        "regime": "unknown",
        "strategy_weights": {
            "breakout": 0.25,
            "smart_money": 0.25,
            "liquidity": 0.2,
            "insider": 0.2,
            "fusion": 0.1,
        },
        "candidates": {},
    }

    for k, v in defaults.items():
        if not hasattr(engine, k):
            setattr(engine, k, v)


# ===== LOG =====
def log(msg):
    print(msg)
    engine.logs.append(str(msg))
    engine.logs = engine.logs[-300:]


# ===== FEATURE（你之後可以接真資料）=====
def build_features(mint):
    return {
        "breakout": random.uniform(0.01, 0.03),
        "smart_money": random.uniform(0.05, 0.25),
        "liquidity": random.uniform(0.05, 0.3),
        "insider": random.uniform(0.0, 0.5),
    }


# ===== BUILD STATS =====
def build_source_stats():
    stats = {}
    for t in engine.trade_history:
        if not isinstance(t, dict):
            continue
        src = (t.get("meta", {}) or {}).get("source", "unknown")
        pnl = float(t.get("pnl", 0.0))

        if src not in stats:
            stats[src] = {"count": 0, "wins": 0, "total_pnl": 0}

        stats[src]["count"] += 1
        stats[src]["total_pnl"] += pnl
        if pnl >= 0:
            stats[src]["wins"] += 1

    for s in stats:
        c = max(stats[s]["count"], 1)
        stats[s]["avg_pnl"] = stats[s]["total_pnl"] / c
        stats[s]["win_rate"] = stats[s]["wins"] / c

    return stats


def build_insider_perf():
    return {
        "high_insider": {"avg_pnl": 0.01, "win_rate": 0.6, "count": 5},
        "low_insider": {"avg_pnl": -0.01, "win_rate": 0.4, "count": 5},
        "comparison": {"avg_pnl_diff": 0.02, "win_rate_diff": 0.2},
    }


# ===== 多策略 =====
def run_strategies(mint, f):
    source_stats = build_source_stats()
    insider_perf = build_insider_perf()

    return {
        "breakout": combine_scores(f["breakout"], 0, 0, 0, engine.regime, source_stats, insider_perf),
        "smart_money": combine_scores(0, f["smart_money"], 0, 0, engine.regime, source_stats, insider_perf),
        "liquidity": combine_scores(0, 0, f["liquidity"], 0, engine.regime, source_stats, insider_perf),
        "insider": combine_scores(0, 0, 0, f["insider"], engine.regime, source_stats, insider_perf),
        "fusion": combine_scores(
            f["breakout"], f["smart_money"], f["liquidity"], f["insider"],
            engine.regime, source_stats, insider_perf
        )
    }


# ===== SELL =====
async def try_sell(pos):
    price = pos["entry"] * (1 + random.uniform(-0.02, 0.05))
    pnl = (price - pos["entry"]) / pos["entry"]

    if pnl >= TAKE_PROFIT or pnl <= STOP_LOSS:
        engine.positions.remove(pos)

        engine.capital += pos["size"]
        engine.capital += pos["size"] * pnl

        engine.trade_history.append({
            "mint": pos["mint"],
            "pnl": pnl,
            "meta": pos.get("meta", {}),
        })

        if pnl >= 0:
            engine.stats["wins"] += 1
        else:
            engine.stats["losses"] += 1

        log(f"SELL {pos['mint']} pnl={pnl:.4f}")


# ===== TRADE =====
async def try_trade(mint):
    if any(p["mint"] == mint for p in engine.positions):
        return

    if len(engine.positions) >= MAX_POSITIONS:
        return

    f = build_features(mint)
    strategies = run_strategies(mint, f)

    best = max(strategies, key=strategies.get)
    score = strategies[best]

    size = get_position_size(score, engine.capital, engine)

    if engine.capital < size:
        return

    engine.capital -= size

    engine.positions.append({
        "mint": mint,
        "entry": 100,
        "size": size,
        "score": score,
        "strategy": best,
        "time": time.time(),
        "meta": {
            "source": best,
            **f,
        },
    })

    engine.stats["executed"] += 1

    log(f"BUY {mint} strat={best} size={size:.4f}")


# ===== AI學習 =====
def update_weights(metrics):
    wr = metrics["performance"]["win_rate"]

    if wr < 0.4:
        for k in engine.strategy_weights:
            engine.strategy_weights[k] *= 0.9
    elif wr > 0.6:
        for k in engine.strategy_weights:
            engine.strategy_weights[k] *= 1.1

    total = sum(engine.strategy_weights.values())
    for k in engine.strategy_weights:
        engine.strategy_weights[k] /= total


# ===== MAIN LOOP =====
async def main_loop():
    ensure_engine()

    if mempool_sniper:
        asyncio.create_task(mempool_sniper(engine))

    log("🔥 V21 FUSION ENGINE START")

    while engine.running:
        try:
            if pump_scanner:
                await pump_scanner(engine)

            mints = list(engine.candidates.keys())[-20:]
            if not mints:
                mints = ["AAA", "BBB", "CCC"]

            for mint in mints:
                await try_trade(mint)

            for pos in list(engine.positions):
                await try_sell(pos)

            if len(engine.trade_history) >= 5:
                m = compute_metrics(engine)
                if m:
                    update_weights(m)

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"ERR {e}")

        await asyncio.sleep(1)
