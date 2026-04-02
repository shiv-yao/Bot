import asyncio
import random
import time
from collections import defaultdict

from app.state import engine
from app.metrics import compute_metrics

# ===== V20 =====
from app.mempool.sniper import mempool_sniper
from app.discovery.pump import pump_scanner

# ===== V19 =====
from app.strategy.engine import run_strategies
from app.strategy.allocator import allocate_capital
from app.strategy.auto_kill import update_strategy_weights

# ===== V17 =====
from app.alpha.combiner import combine_scores

# ===== CONFIG =====
MAX_POSITIONS = 4
TAKE_PROFIT = 0.02
STOP_LOSS = -0.01
TOKEN_COOLDOWN = 10

LAST_TRADE = defaultdict(float)

# ===== INIT =====
def ensure_engine():
    if not hasattr(engine, "positions"):
        engine.positions = []

    if not hasattr(engine, "logs"):
        engine.logs = []

    if not hasattr(engine, "trade_history"):
        engine.trade_history = []

    if not hasattr(engine, "stats"):
        engine.stats = {
            "signals": 0,
            "executed": 0,
            "wins": 0,
            "losses": 0,
            "errors": 0,
        }

    if not hasattr(engine, "capital"):
        engine.capital = 5.0

    if not hasattr(engine, "peak_capital"):
        engine.peak_capital = engine.capital

    if not hasattr(engine, "running"):
        engine.running = True

    if not hasattr(engine, "candidates"):
        engine.candidates = {}

    if not hasattr(engine, "strategy_weights"):
        engine.strategy_weights = {
            "breakout": 0.2,
            "smart_money": 0.2,
            "insider": 0.2,
            "momentum": 0.2,
            "fusion": 0.2,
        }


# ===== LOG =====
def log(msg):
    print(msg)
    engine.logs.append(str(msg))
    engine.logs = engine.logs[-300:]


# ===== FAKE DATA =====
def fake_features():
    return {
        "momentum": random.uniform(0.01, 0.03),
        "wallet": random.uniform(0.05, 0.25),
        "cluster": random.uniform(0.05, 0.3),
        "insider": random.uniform(0.0, 0.2),
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
async def try_trade(item):
    mint = item["mint"]

    if any(p["mint"] == mint for p in engine.positions):
        return

    if len(engine.positions) >= MAX_POSITIONS:
        return

    f = fake_features()

    # ===== 多策略 =====
    strategy_scores = run_strategies({
        "mint": mint,
        "momentum": f["momentum"],
        "wallet": f["wallet"],
        "cluster": f["cluster"],
        "insider": f["insider"],
    }, engine)

    allocations = allocate_capital(engine, strategy_scores)

    best_strat = max(strategy_scores, key=strategy_scores.get)
    score = strategy_scores[best_strat]

    size = engine.capital * allocations.get(best_strat, 0.1)

    if engine.capital < size or size <= 0:
        return

    engine.capital -= size

    engine.positions.append({
        "mint": mint,
        "entry": 100,
        "size": size,
        "score": score,
        "strategy": best_strat,
        "time": time.time(),
        "meta": {
            "source": best_strat,
            **f,
        },
    })

    engine.stats["executed"] += 1

    log(f"BUY {mint} strat={best_strat} size={size:.4f}")


# ===== MAIN LOOP =====
async def main_loop():
    ensure_engine()

    # ===== 背景 =====
    asyncio.create_task(mempool_sniper(engine))

    log("🔥 V20 WAR ENGINE START")

    while engine.running:
        try:
            # ===== pump.fun =====
            await pump_scanner(engine)

            # ===== candidates =====
            items = []
            for mint in list(engine.candidates.keys())[-20:]:
                items.append({"mint": mint})

            for item in items:
                await try_trade(item)

            # ===== manage =====
            for pos in list(engine.positions):
                await try_sell(pos)

            # ===== metrics + AI進化 =====
            if len(engine.trade_history) >= 5:
                m = compute_metrics(engine)

                if m:
                    update_strategy_weights(engine, m)

                    log(
                        f"📊 WR={m['performance']['win_rate']} "
                        f"PF={m['performance']['profit_factor']}"
                    )

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"ERR {e}")

        await asyncio.sleep(1)
