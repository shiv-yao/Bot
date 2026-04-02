import asyncio
import random
import time
from collections import defaultdict

from app.state import engine

# ===== CONFIG =====
BASE_SIZE = 1.0
MAX_POSITIONS = 4

TAKE_PROFIT = 0.02
STOP_LOSS = -0.01
TRAILING_STOP = -0.008
MAX_HOLD_SEC = 20

TOKEN_COOLDOWN = 10
TOP_N = 4

LAST_TRADE = defaultdict(float)
LAST_PRICE = {}

# ===== SINGLE BRAIN WEIGHTS =====
WEIGHTS = {
    "momentum": 0.45,
    "wallet": 0.25,
    "cluster": 0.15,
    "insider": 0.15,
}


# ===== INIT SAFE =====
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
def log(msg: str):
    msg = str(msg)
    print(msg)
    engine.logs.append(msg)
    engine.logs = engine.logs[-300:]


# ===== V14 SIZE ENGINE =====
def get_dynamic_size(score: float, wallet: float, insider: float) -> float:
    # 用資金比例，避免單位爆炸
    size = engine.capital * 0.25

    if score > 0.03:
        size *= 1.5
    elif score > 0.02:
        size *= 1.2

    if wallet > 0.2:
        size *= 1.3

    if insider > 0.15:
        size *= 1.2

    # 防爆倉
    size = min(size, engine.capital * 0.4)

    # 防 0 倉
    size = max(size, 0.01)

    return size


# ===== 動態門檻 =====
def dynamic_threshold():
    n = len(engine.positions)

    if n < 2:
        return 0.012
    elif n < 4:
        return 0.014
    else:
        return 0.018


# ===== MOCK SCANNER =====
async def fetch_candidates():
    base = [
        {"mint": "8F8FLuwv7iL26ecsQ1yXmYKJ6us6Y55QEpJDMFk11Wau", "momentum": 0.020},
        {"mint": "sosd5Q3DutGxMEaukBDmkPgsapMQz59jNjGWmhYcdTQ", "momentum": 0.018},
        {"mint": "SooEj828BSjtgTecBRkqBJ4oquc713yyFZqbCawawoN", "momentum": 0.017},
        {"mint": "sokhCSmzutMPPuNcxG1j6gYLowgiM8mswjJu8FBYm5r", "momentum": 0.020},
    ]
    await asyncio.sleep(0)
    return base[:TOP_N]


# ===== FAKE SIGNAL =====
def fake_wallet_alpha(mint: str) -> float:
    return 0.05 + (sum(ord(c) for c in mint[:6]) % 20) / 100.0


def fake_cluster_score(mint: str) -> float:
    return (sum(ord(c) for c in mint[-6:]) % 10) / 20.0


def fake_insider_score(mint: str) -> float:
    return (sum(ord(c) for c in mint[3:9]) % 10) / 25.0


def compute_score(item):
    mint = item["mint"]

    momentum = float(item.get("momentum", 0.0) or 0.0)
    wallet = fake_wallet_alpha(mint)
    cluster = fake_cluster_score(mint)
    insider = fake_insider_score(mint)

    score = (
        momentum * WEIGHTS["momentum"]
        + wallet * WEIGHTS["wallet"]
        + cluster * WEIGHTS["cluster"]
        + insider * WEIGHTS["insider"]
    )

    return {
        "score": score,
        "momentum": momentum,
        "wallet": wallet,
        "cluster": cluster,
        "insider": insider,
    }


def fake_buy_out(score):
    return int(200 + score * 3000)


def fake_price(entry, age):
    phase = int(age) % 16

    if phase < 4:
        drift = 0.012 * phase
    elif phase < 8:
        drift = 0.05 - 0.01 * (phase - 4)
    elif phase < 12:
        drift = 0.01 - 0.015 * (phase - 8)
    else:
        drift = -0.05 + 0.008 * (phase - 12)

    drift += random.uniform(-0.012, 0.015)
    return max(1, int(entry * (1 + drift)))


async def get_price(mint, entry, age):
    await asyncio.sleep(0)
    return fake_price(entry, age)


# ===== CAPITAL HELPERS =====
def mark_peak_capital():
    if engine.capital > engine.peak_capital:
        engine.peak_capital = engine.capital


# ===== SELL =====
async def try_sell(pos, price):
    entry = pos["entry_out"]
    peak = pos["peak"]

    pnl = (price - entry) / entry
    dd = (price - peak) / peak

    log(f"CHECK {pos['mint'][:6]} pnl={pnl:.4f} dd={dd:.4f}")

    reason = None

    if pnl >= TAKE_PROFIT:
        reason = "TP"
    elif pnl <= STOP_LOSS:
        reason = "SL"
    elif peak > entry and dd <= TRAILING_STOP:
        reason = "TRAIL"
    elif time.time() - pos["time"] > MAX_HOLD_SEC:
        reason = "TIME"

    if not reason:
        return False

    engine.positions.remove(pos)

    # 先回本金，再加盈虧
    engine.capital += pos["size"]
    engine.capital += pos["size"] * pnl

    engine.trade_history.append({
        "mint": pos["mint"],
        "pnl": pnl,
        "reason": reason,
        "score": pos.get("score", 0),
        "meta": pos.get("meta", {}),
    })

    if pnl >= 0:
        engine.stats["wins"] += 1
    else:
        engine.stats["losses"] += 1

    mark_peak_capital()

    log(
        f"SELL {pos['mint'][:6]} {reason} "
        f"pnl={pnl:.4f} size={pos['size']:.4f} cap={engine.capital:.4f}"
    )

    return True


async def manage_positions():
    now = time.time()

    for pos in list(engine.positions):
        age = now - pos["time"]

        price = await get_price(pos["mint"], pos["entry_out"], age)

        if price > pos["peak"]:
            pos["peak"] = price

        await try_sell(pos, price)


# ===== BUY =====
async def try_trade(item):
    mint = item["mint"]
    now = time.time()

    if any(p["mint"] == mint for p in engine.positions):
        return

    if len(engine.positions) >= MAX_POSITIONS:
        log("MAX_POSITIONS")
        return

    if now - LAST_TRADE[mint] < TOKEN_COOLDOWN:
        log(f"COOLDOWN {mint[:6]}")
        return

    engine.stats["signals"] += 1

    f = compute_score(item)
    score = f["score"]

    log(
        f"SCORE {mint[:6]} "
        f"s={score:.4f} "
        f"m={f['momentum']:.4f} "
        f"w={f['wallet']:.4f} "
        f"c={f['cluster']:.4f} "
        f"i={f['insider']:.4f}"
    )

    out = fake_buy_out(score)
    last_price = LAST_PRICE.get(mint)

    if last_price:
        move = abs(out - last_price) / last_price

        if move < 0.003:
            score *= 0.85
            log(f"LOW_VOL {mint[:6]} adj={score:.4f}")

    LAST_PRICE[mint] = out

    thr = dynamic_threshold()

    if score < thr:
        if random.random() < 0.35:
            log(f"FORCE_ENTRY {mint[:6]}")
        else:
            engine.stats["rejected"] += 1
            log(f"REJECT {mint[:6]} thr={thr:.4f}")
            return

    size = get_dynamic_size(score, f["wallet"], f["insider"])

    if size <= 0:
        log(f"SKIP_ZERO_SIZE {mint[:6]}")
        return

    if engine.capital < size:
        log("NO_CAPITAL")
        return

    engine.capital -= size

    log(f"BUY {mint[:6]} size={size:.4f} out={out}")

    engine.positions.append({
        "mint": mint,
        "entry_out": out,
        "size": size,
        "peak": out,
        "time": now,
        "score": score,
        "meta": f,
    })

    LAST_TRADE[mint] = now
    engine.stats["executed"] += 1

    log(f"EXECUTED {mint[:6]}")


# ===== MAIN =====
async def main_loop():
    ensure_engine()
    log("🚀 ENGINE START")

    while engine.running:
        try:
            await manage_positions()

            items = await fetch_candidates()
            ranked = sorted(items, key=lambda x: compute_score(x)["score"], reverse=True)

            for item in ranked:
                await try_trade(item)

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"ERR {e}")

        await asyncio.sleep(2)
