import asyncio
import random
import time
from app.metrics import compute_metrics
from collections import defaultdict

from app.state import engine

# ===== CONFIG =====
MAX_POSITIONS = 4

TAKE_PROFIT = 0.02
STOP_LOSS = -0.01
TRAILING_STOP = -0.008
MAX_HOLD_SEC = 20

TOKEN_COOLDOWN = 10
TOP_N = 4

LAST_TRADE = defaultdict(float)
LAST_PRICE = {}

# ===== STREAK =====
WIN_STREAK = 0
LOSS_STREAK = 0

# ===== WEIGHTS =====
WEIGHTS = {
    "momentum": 0.45,
    "wallet": 0.25,
    "cluster": 0.15,
    "insider": 0.15,
}


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
            "rejected": 0,
        }

    if not hasattr(engine, "capital"):
        engine.capital = 5.0

    if not hasattr(engine, "peak_capital"):
        engine.peak_capital = engine.capital

    if not hasattr(engine, "running"):
        engine.running = True


# ===== LOG =====
def log(msg):
    print(msg)
    engine.logs.append(str(msg))
    engine.logs = engine.logs[-300:]


# ===== SIZE ENGINE (v15) =====
def get_dynamic_size(score, wallet, insider):
    base = engine.capital * 0.25

    if score > 0.03:
        base *= 1.5
    elif score > 0.02:
        base *= 1.2

    if wallet > 0.2:
        base *= 1.3

    if insider > 0.15:
        base *= 1.2

    # ===== WIN BOOST =====
    if WIN_STREAK >= 2:
        boost = min(1 + WIN_STREAK * 0.2, 2.5)
        base *= boost
        log(f"WIN_BOOST x{boost:.2f}")

    # ===== LOSS CUT =====
    if LOSS_STREAK >= 2:
        cut = max(0.4, 1 - LOSS_STREAK * 0.25)
        base *= cut
        log(f"LOSS_CUT x{cut:.2f}")

    # ===== DD PROTECT =====
    dd = 0
    if engine.peak_capital > 0:
        dd = (engine.capital - engine.peak_capital) / engine.peak_capital

    if dd < -0.1:
        base *= 0.5
        log("DD_PROTECT")

    if dd < -0.2:
        base *= 0.3
        log("DD_HARD_PROTECT")

    base = min(base, engine.capital * 0.4)
    base = max(base, 0.01)

    return base


# ===== MOCK =====
async def fetch_candidates():
    base = [
        {"mint": "8F8FLu", "momentum": 0.02},
        {"mint": "sosd5Q", "momentum": 0.018},
        {"mint": "SooEj8", "momentum": 0.017},
        {"mint": "sokhCS", "momentum": 0.02},
    ]
    await asyncio.sleep(0)
    return base[:TOP_N]


def fake_wallet(m): return random.uniform(0.05, 0.25)
def fake_cluster(m): return random.uniform(0.05, 0.3)
def fake_insider(m): return random.uniform(0.0, 0.2)


def compute_score(item):
    m = item["mint"]

    momentum = item["momentum"]
    wallet = fake_wallet(m)
    cluster = fake_cluster(m)
    insider = fake_insider(m)

    score = (
        momentum * WEIGHTS["momentum"]
        + wallet * WEIGHTS["wallet"]
        + cluster * WEIGHTS["cluster"]
        + insider * WEIGHTS["insider"]
    )

    return score, momentum, wallet, cluster, insider


def fake_price(entry):
    return entry * (1 + random.uniform(-0.02, 0.05))


# ===== EXIT =====
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
    })

    if pnl >= 0:
        engine.stats["wins"] += 1
        WIN_STREAK += 1
        LOSS_STREAK = 0
    else:
        engine.stats["losses"] += 1
        LOSS_STREAK += 1
        WIN_STREAK = 0

    if engine.capital > engine.peak_capital:
        engine.peak_capital = engine.capital

    log(f"SELL {pos['mint']} pnl={pnl:.4f} cap={engine.capital:.4f}")


# ===== ADD WINNER (v16) =====
def try_add_position(pos):
    if pos.get("added"):
        return

    if random.random() < 0.3:
        size = pos["size"] * 0.5

        if engine.capital < size:
            return

        engine.capital -= size
        pos["size"] += size
        pos["added"] = True

        log(f"ADD {pos['mint']} size={size:.4f}")


# ===== PARTIAL TP (v16) =====
def try_partial(pos):
    if pos.get("tp_done"):
        return

    price = fake_price(pos["entry"])
    pnl = (price - pos["entry"]) / pos["entry"]

    if pnl > 0.015:
        size = pos["size"] * 0.5

        engine.capital += size * (1 + pnl)
        pos["size"] *= 0.5
        pos["tp_done"] = True

        log(f"PARTIAL {pos['mint']} pnl={pnl:.4f}")


# ===== TRADE =====
async def try_trade(item):
    mint = item["mint"]

    if any(p["mint"] == mint for p in engine.positions):
        return

    if len(engine.positions) >= MAX_POSITIONS:
        return

    score, m, w, c, i = compute_score(item)

    log(f"SCORE {mint} s={score:.4f}")

    size = get_dynamic_size(score, w, i)

    if engine.capital < size:
        log("NO_CAPITAL")
        return

    engine.capital -= size

    engine.positions.append({
        "mint": mint,
        "entry": 100,
        "size": size,
        "added": False,
        "tp_done": False,
    })

    log(f"BUY {mint} size={size:.4f}")


# ===== LOOP =====
async def main_loop():
    ensure_engine()
    log("🚀 V16 START")

    while engine.running:
        try:
            for pos in list(engine.positions):
                try_partial(pos)
                try_add_position(pos)
                await try_sell(pos)

            items = await fetch_candidates()

            for item in items:
                await try_trade(item)

            # ===== 📊 METRICS（加在這裡）=====
            if len(engine.trade_history) >= 5:
                m = compute_metrics(engine)

                log(
                    f"📊 trades={m['trades']} "
                    f"wr={m['win_rate']} "
                    f"pf={m['profit_factor']} "
                    f"dd={m['max_drawdown']} "
                    f"sharpe={m['sharpe']}"
                )

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"ERR {e}")

        await asyncio.sleep(2)
        




