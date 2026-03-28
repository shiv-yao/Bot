import asyncio
import random
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI

# ================= CONFIG =================

USE_REAL_EXECUTION = False

STOP_LOSS = -0.07
DAILY_STOP = -0.06

MAX_POSITIONS = 6
MAX_DAILY_TRADES = 40
MAX_HOLD_SECONDS = 240

MAX_POSITION_PER_ENGINE = 3
MAX_POSITION_SIZE = 0.01

# ================= STATE =================

STATE = {
    "positions": [],
    "closed_trades": [],
    "signals": 0,
    "errors": 0,
    "last_action": None,

    "realized_pnl": 0.0,
    "daily_pnl": 0.0,
    "daily_trades": 0,
    "last_reset": time.time(),

    "loss_streak": 0,
    "regime": "chop",

    "alpha_memory": {
        "stable": [],
        "degen": [],
        "sniper": []
    },

    "engine_stats": {
        "stable": {"pnl": 0, "trades": 0, "wins": 0},
        "degen": {"pnl": 0, "trades": 0, "wins": 0},
        "sniper": {"pnl": 0, "trades": 0, "wins": 0},
    },

    "allocator": {
        "stable": 0.4,
        "degen": 0.4,
        "sniper": 0.2,
    },

    "trade_log": [],

    "bot_version": "v15_warfare"
}

# ================= SAFE =================

def safe_div(a, b):
    return a / b if b != 0 else 0

# ================= REGIME =================

def detect_regime():
    if STATE["daily_pnl"] > 0.02:
        return "bull"
    if STATE["loss_streak"] >= 4:
        return "bear"
    return "chop"

# ================= ALPHA =================

def get_real_alpha():
    momentum = random.uniform(-1, 1)
    liquidity = random.uniform(0, 1)
    volatility = random.uniform(0, 1)

    return round(momentum * 35 + liquidity * 40 - volatility * 25, 2)

# ================= ALPHA MEMORY =================

def update_alpha_memory(engine, alpha, pnl):
    mem = STATE["alpha_memory"][engine]
    mem.append((alpha, pnl))
    if len(mem) > 50:
        mem.pop(0)

def get_alpha_edge(engine, alpha):
    mem = STATE["alpha_memory"][engine]
    if not mem:
        return 1.0

    similar = [p for a, p in mem if abs(a - alpha) < 10]
    if not similar:
        return 1.0

    avg = sum(similar) / len(similar)
    return max(0.5, min(1.5, 1 + avg * 5))

# ================= ENGINE SCORE =================

def engine_score(engine):
    s = STATE["engine_stats"][engine]

    if s["trades"] < 3:
        return 1.0

    winrate = safe_div(s["wins"], s["trades"])
    score = (s["pnl"] + 0.001) * winrate

    return max(0.1, min(2.0, score))

# ================= ALLOCATOR =================

def update_allocator():
    weights = {}

    for e in ["stable", "degen", "sniper"]:
        weights[e] = engine_score(e)

    total = sum(weights.values()) + 1e-6

    for e in weights:
        weights[e] /= total

    if STATE["regime"] == "bull":
        weights["degen"] += 0.2
        weights["sniper"] += 0.1
    elif STATE["regime"] == "bear":
        weights["stable"] += 0.3

    total = sum(weights.values())
    for e in weights:
        weights[e] = max(weights[e] / total, 0.05)

    STATE["allocator"] = weights

# ================= SIZE =================

def get_size(alpha, engine):
    base = {"stable": 0.003, "degen": 0.002, "sniper": 0.0015}[engine]

    size = base * STATE["allocator"][engine] * (1 + alpha / 50) * get_alpha_edge(engine, alpha)

    if STATE["loss_streak"] >= 2:
        size *= 0.5

    return min(max(0.0005, round(size, 4)), MAX_POSITION_SIZE)

# ================= EXECUTION =================

async def execute_trade(engine, alpha, size):
    if size <= 0:
        return None

    # 模擬
    if not USE_REAL_EXECUTION:
        price = random.uniform(0.00001, 0.00002)
        qty = safe_div(size, price)

        trade = {
            "engine": engine,
            "alpha": alpha,
            "price": price,
            "qty": qty,
            "time": time.time()
        }

        STATE["trade_log"].append(trade)
        return trade

    # 未來：接 Jupiter
    return None

# ================= RISK =================

def can_open(engine):
    if sum(1 for p in STATE["positions"] if p["engine"] == engine) >= MAX_POSITION_PER_ENGINE:
        return False
    return True

# ================= SELL =================

async def simulate_sell(pos):
    price = pos["entry_price"] * random.uniform(0.6, 1.6)

    value = pos["qty"] * price
    entry = pos["qty"] * pos["entry_price"]

    pnl = value - entry
    pnl_pct = safe_div(pnl, entry)

    return pnl, pnl_pct

# ================= MONITOR =================

async def monitor():
    new_positions = []

    for pos in STATE["positions"]:
        pnl, pnl_pct = await simulate_sell(pos)

        pos["peak"] = max(pos.get("peak", 0), pnl_pct)

        giveback = 0.05 + pos["alpha"] / 200

        if (
            pnl_pct < STOP_LOSS
            or (pos["peak"] > 0.08 and pos["peak"] - pnl_pct > giveback)
            or time.time() - pos["entry_time"] > MAX_HOLD_SECONDS
        ):
            engine = pos["engine"]

            STATE["closed_trades"].append(pos)
            STATE["realized_pnl"] += pnl
            STATE["daily_pnl"] += pnl

            update_alpha_memory(engine, pos["alpha"], pnl)

            st = STATE["engine_stats"][engine]
            st["trades"] += 1
            st["pnl"] += pnl

            if pnl > 0:
                st["wins"] += 1
                STATE["loss_streak"] = 0
            else:
                STATE["loss_streak"] += 1

            continue

        new_positions.append(pos)

    STATE["positions"] = new_positions

# ================= LOOP =================

async def bot_loop():
    while True:
        try:
            if STATE["daily_pnl"] < DAILY_STOP:
                await asyncio.sleep(5)
                continue

            STATE["regime"] = detect_regime()
            update_allocator()

            await monitor()

            if random.random() > 0.6:
                await asyncio.sleep(2)
                continue

            for _ in range(20):
                if len(STATE["positions"]) >= MAX_POSITIONS:
                    break

                engine = random.choices(
                    ["stable", "degen", "sniper"],
                    weights=list(STATE["allocator"].values())
                )[0]

                if not can_open(engine):
                    continue

                alpha = get_real_alpha()

                if engine == "stable" and alpha < 8:
                    continue
                if engine == "degen" and alpha < 25:
                    continue
                if engine == "sniper" and alpha < 45:
                    continue

                size = get_size(alpha, engine)

                trade = await execute_trade(engine, alpha, size)
                if not trade or trade["qty"] <= 0:
                    continue

                STATE["positions"].append({
                    "token": f"TOKEN{random.randint(1,1000)}",
                    "entry_price": trade["price"],
                    "qty": trade["qty"],
                    "alpha": alpha,
                    "engine": engine,
                    "entry_time": time.time()
                })

        except Exception as e:
            STATE["errors"] += 1
            STATE["last_action"] = str(e)

        await asyncio.sleep(2)

# ================= API =================

bot_task = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot_task
    bot_task = asyncio.create_task(bot_loop())
    yield
    bot_task.cancel()

app = FastAPI(lifespan=lifespan)

@app.get("/")
def root():
    return {"ok": True}

@app.get("/metrics")
def metrics():
    return STATE
