import asyncio
import random
import time
from collections import defaultdict

from app.metrics import compute_metrics
from app.state import engine
from app.alpha.combiner import combine_scores
from app.portfolio.allocator import get_position_size

MAX_POSITIONS = 4
TAKE_PROFIT = 0.02
STOP_LOSS = -0.01
TRAILING_STOP = -0.008
MAX_HOLD_SEC = 20

TOKEN_COOLDOWN = 10
TOP_N = 4

LAST_TRADE = defaultdict(float)
LAST_PRICE = {}

WIN_STREAK = 0
LOSS_STREAK = 0


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
            "rejected": 0,
        }
    if not hasattr(engine, "capital"):
        engine.capital = 5.0
    if not hasattr(engine, "start_capital"):
        engine.start_capital = engine.capital
    if not hasattr(engine, "peak_capital"):
        engine.peak_capital = engine.capital
    if not hasattr(engine, "running"):
        engine.running = True
    if not hasattr(engine, "regime"):
        engine.regime = "unknown"
    if not hasattr(engine, "win_streak"):
        engine.win_streak = 0
    if not hasattr(engine, "loss_streak"):
        engine.loss_streak = 0
    if not hasattr(engine, "last_signal"):
        engine.last_signal = ""
    if not hasattr(engine, "last_trade"):
        engine.last_trade = ""


def log(msg):
    msg = str(msg)
    print(msg)
    engine.logs.append(msg)
    engine.logs = engine.logs[-300:]


async def fetch_candidates():
    base = [
        {"mint": "8F8FLu", "momentum": 0.020},
        {"mint": "sosd5Q", "momentum": 0.018},
        {"mint": "SooEj8", "momentum": 0.017},
        {"mint": "sokhCS", "momentum": 0.020},
    ]
    await asyncio.sleep(0)
    return base[:TOP_N]


def fake_wallet(_m):
    return random.uniform(0.05, 0.25)


def fake_cluster(_m):
    return random.uniform(0.05, 0.3)


def fake_insider(_m):
    return random.uniform(0.0, 0.5)


def _build_source_stats():
    stats = {}
    for t in engine.trade_history:
        if not isinstance(t, dict):
            continue
        src = (t.get("meta", {}) or {}).get("source", "unknown")
        pnl = float(t.get("pnl", 0.0) or 0.0)

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


def _build_insider_perf(threshold: float = 0.10):
    buckets = {
        "high_insider": {"count": 0, "wins": 0, "losses": 0, "total_pnl": 0.0},
        "low_insider": {"count": 0, "wins": 0, "losses": 0, "total_pnl": 0.0},
    }

    for t in engine.trade_history:
        if not isinstance(t, dict):
            continue
        meta = t.get("meta", {}) or {}
        pnl = float(t.get("pnl", 0.0) or 0.0)
        insider = float(meta.get("insider", 0.0) or 0.0)

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


def compute_score(item):
    mint = item["mint"]

    breakout = float(item["momentum"])
    smart_money = float(fake_wallet(mint))
    liquidity = float(fake_cluster(mint))
    insider = float(fake_insider(mint))

    source_stats = _build_source_stats()
    insider_perf = _build_insider_perf()

    score = combine_scores(
        breakout=breakout,
        smart_money=smart_money,
        liquidity=liquidity,
        insider=insider,
        regime=getattr(engine, "regime", "unknown"),
        source_stats=source_stats,
        insider_perf=insider_perf,
    )

    return score, breakout, smart_money, liquidity, insider


def fake_price(entry):
    return entry * (1 + random.uniform(-0.02, 0.05))


async def try_sell(pos):
    global WIN_STREAK, LOSS_STREAK

    price = fake_price(pos["entry"])
    pnl = (price - pos["entry"]) / pos["entry"]

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
        WIN_STREAK += 1
        LOSS_STREAK = 0
    else:
        engine.stats["losses"] += 1
        LOSS_STREAK += 1
        WIN_STREAK = 0

    engine.win_streak = WIN_STREAK
    engine.loss_streak = LOSS_STREAK
    engine.last_trade = f"{pos['mint']} {reason} pnl={pnl:.4f}"

    if engine.capital > engine.peak_capital:
        engine.peak_capital = engine.capital

    log(f"SELL {pos['mint']} {reason} pnl={pnl:.4f} cap={engine.capital:.4f}")


def try_add_position(pos):
    if pos.get("added"):
        return

    if random.random() < 0.3:
        size = round(pos["size"] * 0.5, 4)

        if engine.capital < size:
            return

        engine.capital -= size
        pos["size"] += size
        pos["added"] = True

        log(f"ADD {pos['mint']} size={size:.4f}")


def try_partial(pos):
    if pos.get("tp_done"):
        return

    price = fake_price(pos["entry"])
    pnl = (price - pos["entry"]) / pos["entry"]

    if pnl > 0.015:
        size = round(pos["size"] * 0.5, 4)

        engine.capital += size
        engine.capital += size * pnl
        pos["size"] *= 0.5
        pos["tp_done"] = True

        engine.trade_history.append({
            "mint": pos["mint"],
            "pnl": pnl,
            "reason": "PARTIAL",
            "score": pos.get("score", 0.0),
            "size": size,
            "timestamp": time.time(),
            "meta": pos.get("meta", {}),
        })

        if engine.capital > engine.peak_capital:
            engine.peak_capital = engine.capital

        log(f"PARTIAL {pos['mint']} pnl={pnl:.4f}")


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
        log(f"COOLDOWN {mint}")
        return

    score, breakout, smart_money, liquidity, insider = compute_score(item)
    engine.stats["signals"] += 1
    engine.last_signal = f"{mint} score={score:.4f}"

    log(
        f"SCORE {mint} "
        f"s={score:.4f} "
        f"b={breakout:.4f} "
        f"sm={smart_money:.4f} "
        f"l={liquidity:.4f} "
        f"i={insider:.4f}"
    )

    size = get_position_size(score, engine.capital, engine)

    if size <= 0:
        engine.stats["rejected"] += 1
        log(f"SKIP_ZERO_SIZE {mint}")
        return

    if engine.capital < size:
        engine.stats["rejected"] += 1
        log("NO_CAPITAL")
        return

    engine.capital -= size

    engine.positions.append({
        "mint": mint,
        "entry": 100.0,
        "size": size,
        "added": False,
        "tp_done": False,
        "score": score,
        "time": now,
        "meta": {
            "source": "fusion",
            "breakout": breakout,
            "smart_money": smart_money,
            "liquidity": liquidity,
            "momentum": breakout,
            "insider": insider,
        },
    })

    LAST_TRADE[mint] = now
    engine.stats["executed"] += 1

    log(f"BUY {mint} size={size:.4f}")


async def main_loop():
    ensure_engine()
    log("🚀 V18 START")

    while engine.running:
        try:
            for pos in list(engine.positions):
                try_partial(pos)
                try_add_position(pos)
                await try_sell(pos)

            items = await fetch_candidates()
            for item in items:
                await try_trade(item)

            if len(engine.trade_history) >= 5:
                m = compute_metrics(engine)
                if m:
                    log(
                        f"📊 trades={m.get('performance', {}).get('trades', 0)} "
                        f"wr={m.get('performance', {}).get('win_rate', 0)} "
                        f"pf={m.get('performance', {}).get('profit_factor', 0)} "
                        f"dd={m.get('summary', {}).get('drawdown', 0)} "
                        f"sharpe={m.get('performance', {}).get('sharpe', 0)}"
                    )

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"ERR {e}")

        await asyncio.sleep(2)
