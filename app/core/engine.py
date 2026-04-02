import asyncio
import random
import time
from collections import defaultdict

from app.state import engine
from app.metrics import compute_metrics
from app.alpha.combiner import combine_scores

# ===== CONFIG =====
MAX_POSITIONS = 4
TAKE_PROFIT = 0.02
STOP_LOSS = -0.01
TOKEN_COOLDOWN = 10

LAST_TRADE = defaultdict(float)

# ===== INIT =====
def ensure_engine():
    engine.positions = getattr(engine, "positions", [])
    engine.logs = getattr(engine, "logs", [])
    engine.trade_history = getattr(engine, "trade_history", [])

    engine.stats = getattr(engine, "stats", {
        "signals": 0,
        "executed": 0,
        "wins": 0,
        "losses": 0,
        "errors": 0,
        "rejected": 0,
    })

    engine.capital = getattr(engine, "capital", 5.0)
    engine.peak_capital = getattr(engine, "peak_capital", engine.capital)
    engine.running = getattr(engine, "running", True)

    # ⭐ 多策略權重
    engine.strategy_weights = getattr(engine, "strategy_weights", {
        "breakout": 0.25,
        "smart_money": 0.25,
        "liquidity": 0.2,
        "insider": 0.15,
        "fusion": 0.15,
    })


# ===== LOG =====
def log(msg):
    msg = str(msg)
    print(msg)
    engine.logs.append(msg)
    engine.logs = engine.logs[-300:]


# ===== FAKE FEATURES（可替換真資料）=====
def fake_features():
    return {
        "breakout": random.uniform(0.01, 0.03),
        "smart_money": random.uniform(0.05, 0.25),
        "liquidity": random.uniform(0.05, 0.3),
        "insider": random.uniform(0.0, 0.2),
    }


# ===== 多策略引擎 =====
def run_strategies(features):
    strategies = {}

    strategies["breakout"] = features["breakout"]
    strategies["smart_money"] = features["smart_money"]
    strategies["liquidity"] = features["liquidity"]
    strategies["insider"] = features["insider"]

    # ⭐ AI fusion
    strategies["fusion"] = combine_scores(
        features["breakout"],
        features["smart_money"],
        features["liquidity"],
        features["insider"],
        getattr(engine, "regime", "unknown"),
        {},
        {},
    )

    return strategies


# ===== 資金分配 =====
def allocate_capital(strategy_scores):
    total = sum(max(v, 0) for v in strategy_scores.values()) or 1
    allocation = {}

    for k, score in strategy_scores.items():
        base = score / total
        weight = engine.strategy_weights.get(k, 0.2)
        allocation[k] = base * weight

    # normalize
    s = sum(allocation.values()) or 1
    for k in allocation:
        allocation[k] /= s

    return allocation


# ===== AI 自我進化 =====
def update_strategy_weights(metrics):
    wr = metrics["performance"]["win_rate"]

    for k in engine.strategy_weights:
        if wr < 0.45:
            engine.strategy_weights[k] *= 0.9
        elif wr > 0.6:
            engine.strategy_weights[k] *= 1.1

    # normalize
    total = sum(engine.strategy_weights.values()) or 1
    for k in engine.strategy_weights:
        engine.strategy_weights[k] /= total


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

    now = time.time()
    if now - LAST_TRADE[mint] < TOKEN_COOLDOWN:
        return

    f = fake_features()

    strategy_scores = run_strategies(f)
    allocation = allocate_capital(strategy_scores)

    best_strategy = max(strategy_scores, key=strategy_scores.get)
    score = strategy_scores[best_strategy]

    size = engine.capital * allocation.get(best_strategy, 0.1)

    if size <= 0 or engine.capital < size:
        return

    engine.capital -= size

    engine.positions.append({
        "mint": mint,
        "entry": 100.0,
        "size": size,
        "score": score,
        "strategy": best_strategy,
        "time": now,
        "meta": {
            "source": best_strategy,
            **f,
        },
    })

    LAST_TRADE[mint] = now
    engine.stats["executed"] += 1

    log(f"BUY {mint} strat={best_strategy} size={size:.4f}")


# ===== MAIN LOOP =====
async def main_loop():
    ensure_engine()
    log("🔥 V22 FUND ENGINE START")

    while engine.running:
        try:
            # ===== 模擬 candidates =====
            mints = ["AAA", "BBB", "CCC", "DDD"]

            for mint in mints:
                await try_trade(mint)

            for pos in list(engine.positions):
                await try_sell(pos)

            # ===== AI進化 =====
            if len(engine.trade_history) >= 10:
                m = compute_metrics(engine)
                if m:
                    update_strategy_weights(m)

                    log(
                        f"📊 WR={m['performance']['win_rate']} "
                        f"PF={m['performance']['profit_factor']}"
                    )

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"ERR {e}")

        await asyncio.sleep(2)
