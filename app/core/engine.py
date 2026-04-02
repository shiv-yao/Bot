import asyncio
import time
from collections import defaultdict

from app.state import engine
from app.metrics import compute_metrics
from app.alpha.combiner import combine_scores

# 你貼的真數據模組
from app.sources.pump import fetch_pump_candidates
from app.data.market import get_quote
from app.alpha.helius_wallet_tracker import update_token_wallets, token_wallets

# ===== CONFIG =====
MAX_POSITIONS = 4
TAKE_PROFIT = 0.02
STOP_LOSS = -0.01
TOKEN_COOLDOWN = 10

SOL_MINT = "So11111111111111111111111111111111111111112"
QUOTE_AMOUNT_IN = 1_000_000  # 0.001 SOL, 可自己調

LAST_TRADE = defaultdict(float)
LAST_QUOTE = {}


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
    engine.start_capital = getattr(engine, "start_capital", engine.capital)
    engine.peak_capital = getattr(engine, "peak_capital", engine.capital)
    engine.running = getattr(engine, "running", True)
    engine.regime = getattr(engine, "regime", "unknown")

    engine.strategy_weights = getattr(engine, "strategy_weights", {
        "breakout": 0.25,
        "smart_money": 0.25,
        "liquidity": 0.2,
        "insider": 0.15,
        "fusion": 0.15,
    })

    engine.source_stats = getattr(engine, "source_stats", {})
    engine.insider_perf = getattr(engine, "insider_perf", {})


# ===== LOG =====
def log(msg):
    msg = str(msg)
    print(msg)
    engine.logs.append(msg)
    engine.logs = engine.logs[-300:]


# ===== HELPERS =====
def safe_float(x, default=0.0):
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def build_source_stats():
    stats = {}
    for t in engine.trade_history:
        if not isinstance(t, dict):
            continue

        src = (t.get("meta", {}) or {}).get("source", "unknown")
        pnl = safe_float(t.get("pnl", 0.0))

        if src not in stats:
            stats[src] = {
                "count": 0,
                "wins": 0,
                "losses": 0,
                "total_pnl": 0.0,
                "avg_pnl": 0.0,
                "win_rate": 0.0,
            }

        stats[src]["count"] += 1
        stats[src]["total_pnl"] += pnl

        if pnl >= 0:
            stats[src]["wins"] += 1
        else:
            stats[src]["losses"] += 1

    for src, row in stats.items():
        c = max(row["count"], 1)
        row["avg_pnl"] = row["total_pnl"] / c
        row["win_rate"] = row["wins"] / c

    return stats


def build_insider_perf(threshold: float = 0.10):
    buckets = {
        "high_insider": {
            "count": 0,
            "wins": 0,
            "losses": 0,
            "total_pnl": 0.0,
            "avg_pnl": 0.0,
            "win_rate": 0.0,
        },
        "low_insider": {
            "count": 0,
            "wins": 0,
            "losses": 0,
            "total_pnl": 0.0,
            "avg_pnl": 0.0,
            "win_rate": 0.0,
        },
    }

    for t in engine.trade_history:
        if not isinstance(t, dict):
            continue

        meta = t.get("meta", {}) or {}
        pnl = safe_float(t.get("pnl", 0.0))
        insider = safe_float(meta.get("insider", 0.0))

        name = "high_insider" if insider >= threshold else "low_insider"
        row = buckets[name]

        row["count"] += 1
        row["total_pnl"] += pnl
        if pnl >= 0:
            row["wins"] += 1
        else:
            row["losses"] += 1

    for row in buckets.values():
        c = max(row["count"], 1)
        row["avg_pnl"] = row["total_pnl"] / c
        row["win_rate"] = row["wins"] / c

    buckets["comparison"] = {
        "avg_pnl_diff": buckets["high_insider"]["avg_pnl"] - buckets["low_insider"]["avg_pnl"],
        "win_rate_diff": buckets["high_insider"]["win_rate"] - buckets["low_insider"]["win_rate"],
        "threshold": threshold,
    }

    return buckets


# ===== 真數據特徵 =====
async def build_real_features(mint: str):
    # 1) 真 wallet / smart money
    wallets = await update_token_wallets(mint)
    wallet_count = len(token_wallets.get(mint, set()))

    # 2) 真 quote
    q = await get_quote(SOL_MINT, mint, QUOTE_AMOUNT_IN)

    if not q:
        return None

    out_amount = safe_float(q.get("outAmount", 0.0))
    price_impact_pct = safe_float(q.get("priceImpactPct", 0.0))

    # 3) 簡單 momentum：跟上一次 quote 比
    prev = LAST_QUOTE.get(mint)
    momentum = 0.0
    if prev and prev > 0 and out_amount > 0:
        momentum = (out_amount - prev) / prev

    LAST_QUOTE[mint] = out_amount

    # 4) insider：先用 wallet 濃度近似，後面可再升級成真正 insider engine
    insider = min(wallet_count / 20.0, 1.0)

    # 5) liquidity：先用 outAmount / priceImpact 近似
    liquidity = 0.0
    if out_amount > 0:
        liquidity = min(out_amount / 100000.0, 1.0)

    # 6) smart money：先用 wallet 數量近似
    smart_money = min(wallet_count / 10.0, 1.0)

    # 7) breakout：先用 momentum
    breakout = max(momentum, 0.0)

    return {
        "breakout": breakout,
        "smart_money": smart_money,
        "liquidity": liquidity,
        "insider": insider,
        "wallet_count": wallet_count,
        "out_amount": out_amount,
        "price_impact_pct": price_impact_pct,
    }


# ===== 多策略 =====
def run_strategies(features):
    strategies = {}

    strategies["breakout"] = features["breakout"]
    strategies["smart_money"] = features["smart_money"]
    strategies["liquidity"] = features["liquidity"]
    strategies["insider"] = features["insider"]

    strategies["fusion"] = combine_scores(
        features["breakout"],
        features["smart_money"],
        features["liquidity"],
        features["insider"],
        getattr(engine, "regime", "unknown"),
        getattr(engine, "source_stats", {}),
        getattr(engine, "insider_perf", {}),
    )

    return strategies


# ===== allocator =====
def allocate_capital(strategy_scores):
    total = sum(max(v, 0.0) for v in strategy_scores.values()) or 1.0
    allocation = {}

    for k, score in strategy_scores.items():
        base = score / total
        weight = engine.strategy_weights.get(k, 0.2)
        allocation[k] = base * weight

    s = sum(allocation.values()) or 1.0
    for k in allocation:
        allocation[k] /= s

    return allocation


# ===== AI進化 =====
def update_strategy_weights(metrics):
    wr = safe_float(metrics.get("performance", {}).get("win_rate", 0.0))

    for k in engine.strategy_weights:
        if wr < 0.45:
            engine.strategy_weights[k] *= 0.9
        elif wr > 0.6:
            engine.strategy_weights[k] *= 1.1

    total = sum(engine.strategy_weights.values()) or 1.0
    for k in engine.strategy_weights:
        engine.strategy_weights[k] /= total


# ===== 真價格 =====
async def get_live_position_pnl(pos):
    mint = pos["mint"]
    entry_out = safe_float(pos.get("entry_out", 0.0))
    if entry_out <= 0:
        return None

    q = await get_quote(SOL_MINT, mint, QUOTE_AMOUNT_IN)
    if not q:
        return None

    out_amount = safe_float(q.get("outAmount", 0.0))
    if out_amount <= 0:
        return None

    pnl = (out_amount - entry_out) / entry_out
    return pnl, out_amount


# ===== SELL =====
async def try_sell(pos):
    live = await get_live_position_pnl(pos)
    if not live:
        return

    pnl, current_out = live

    if pnl >= TAKE_PROFIT:
        reason = "TP"
    elif pnl <= STOP_LOSS:
        reason = "SL"
    else:
        return

    engine.positions.remove(pos)

    engine.capital += pos["size"]
    engine.capital += pos["size"] * pnl

    engine.trade_history.append({
        "mint": pos["mint"],
        "pnl": pnl,
        "reason": reason,
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

    log(f"SELL {pos['mint']} {reason} pnl={pnl:.4f} cap={engine.capital:.4f}")


# ===== TRADE =====
async def try_trade(item):
    mint = item["mint"]

    if any(p["mint"] == mint for p in engine.positions):
        return

    if len(engine.positions) >= MAX_POSITIONS:
        engine.stats["rejected"] += 1
        log("MAX_POSITIONS")
        return

    now = time.time()
    if now - LAST_TRADE[mint] < TOKEN_COOLDOWN:
        engine.stats["rejected"] += 1
        log(f"COOLDOWN {mint[:8]}")
        return

    features = await build_real_features(mint)
    if not features:
        engine.stats["rejected"] += 1
        log(f"NO_FEATURES {mint[:8]}")
        return

    strategy_scores = run_strategies(features)
    allocation = allocate_capital(strategy_scores)

    best_strategy = max(strategy_scores, key=strategy_scores.get)
    score = strategy_scores[best_strategy]

    size = engine.capital * allocation.get(best_strategy, 0.1)

    if size <= 0 or engine.capital < size:
        engine.stats["rejected"] += 1
        log(f"NO_CAPITAL {mint[:8]}")
        return

    entry_out = safe_float(features["out_amount"], 0.0)
    if entry_out <= 0:
        engine.stats["rejected"] += 1
        log(f"NO_ROUTE {mint[:8]}")
        return

    engine.capital -= size

    engine.positions.append({
        "mint": mint,
        "entry": 100.0,  # 保留舊欄位相容
        "entry_out": entry_out,
        "size": size,
        "score": score,
        "strategy": best_strategy,
        "time": now,
        "meta": {
            "source": best_strategy,
            "breakout": features["breakout"],
            "smart_money": features["smart_money"],
            "liquidity": features["liquidity"],
            "insider": features["insider"],
            "wallet_count": features["wallet_count"],
            "price_impact_pct": features["price_impact_pct"],
        },
    })

    LAST_TRADE[mint] = now
    engine.stats["signals"] += 1
    engine.stats["executed"] += 1
    engine.last_signal = f"{mint[:8]} strat={best_strategy} score={score:.4f}"

    log(
        f"BUY {mint[:8]} "
        f"strat={best_strategy} "
        f"size={size:.4f} "
        f"out={entry_out:.0f} "
        f"wc={features['wallet_count']}"
    )


# ===== MAIN LOOP =====
async def main_loop():
    ensure_engine()
    log("🔥 V22 REAL-DATA ENGINE START")

    while engine.running:
        try:
            candidates = await fetch_pump_candidates()

            engine.source_stats = build_source_stats()
            engine.insider_perf = build_insider_perf()

            for item in candidates:
                await try_trade(item)

            for pos in list(engine.positions):
                await try_sell(pos)

            if len(engine.trade_history) >= 10:
                m = compute_metrics(engine)
                if m:
                    update_strategy_weights(m)
                    log(
                        f"📊 WR={m['performance']['win_rate']} "
                        f"PF={m['performance']['profit_factor']} "
                        f"DD={m['performance']['max_drawdown']}"
                    )

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"ERR {e}")

        await asyncio.sleep(5)
