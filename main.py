import asyncio
import random
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI

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

    # ===== alpha memory =====
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

    "bot_version": "v10_god_brain"
}

# ================= CONFIG =================

STOP_LOSS = -0.07
DAILY_STOP = -0.06

MAX_POSITIONS = 6
MAX_DAILY_TRADES = 40
MAX_HOLD_SECONDS = 240

# ================= 🧠 REGIME =================

def detect_regime():
    pnl = STATE["daily_pnl"]
    loss = STATE["loss_streak"]

    if pnl > 0.02:
        return "bull"
    if loss >= 4:
        return "bear"
    return "chop"

# ================= 🧠 ALPHA MEMORY =================

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

# ================= 🧠 ALLOCATOR =================

def update_allocator():
    stats = STATE["engine_stats"]

    weights = {}

    for e in ["stable", "degen", "sniper"]:
        s = stats[e]
        if s["trades"] == 0:
            weights[e] = 1
        else:
            winrate = s["wins"] / s["trades"]
            weights[e] = (s["pnl"] + 0.001) * winrate

    total = sum(abs(w) for w in weights.values()) + 1e-6

    for e in weights:
        weights[e] = abs(weights[e]) / total

    # ===== regime bias =====
    if STATE["regime"] == "bull":
        weights["degen"] += 0.2
        weights["sniper"] += 0.1
    elif STATE["regime"] == "bear":
        weights["stable"] += 0.3

    total = sum(weights.values())
    for e in weights:
        weights[e] /= total

    STATE["allocator"] = weights

# ================= SIZE =================

def get_size(alpha, engine):
    base = {
        "stable": 0.003,
        "degen": 0.002,
        "sniper": 0.0015
    }[engine]

    weight = STATE["allocator"][engine]

    edge = get_alpha_edge(engine, alpha)

    size = base * weight * (1 + alpha / 50) * edge

    if STATE["loss_streak"] >= 2:
        size *= 0.5

    return round(size, 4)

# ================= EXEC =================

async def simulate_buy(size):
    price = random.uniform(0.00001, 0.00002)
    return price, size / price

async def simulate_sell(pos):
    price = pos["entry_price"] * random.uniform(0.6, 1.6)

    pnl = (price - pos["entry_price"]) * pos["qty"]
    pnl_pct = pnl / (pos["entry_price"] * pos["qty"])

    return price, pnl, pnl_pct

# ================= MONITOR =================

async def monitor():
    new_positions = []

    for pos in STATE["positions"]:
        price, pnl, pnl_pct = await simulate_sell(pos)

        pos["pnl_pct"] = pnl_pct
        pos["peak"] = max(pos.get("peak", 0), pnl_pct)

        # ===== TP1 =====
        if not pos.get("tp1") and pnl_pct > 0.1:
            pos["tp1"] = True
            pos["qty"] *= 0.5

        # ===== trailing =====
        giveback = 0.06
        if pos["peak"] > 0.3:
            giveback = 0.1
        if pos["peak"] > 0.6:
            giveback = 0.2

        timeout = time.time() - pos["entry_time"] > MAX_HOLD_SECONDS

        if (
            pnl_pct < STOP_LOSS
            or (pos["peak"] > 0.08 and pos["peak"] - pnl_pct > giveback)
            or timeout
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
            now = time.time()

            if now - STATE["last_reset"] > 86400:
                STATE["daily_pnl"] = 0
                STATE["daily_trades"] = 0
                STATE["loss_streak"] = 0
                STATE["last_reset"] = now

            if STATE["daily_pnl"] < DAILY_STOP:
                STATE["last_action"] = "daily_stop"
                await asyncio.sleep(5)
                continue

            STATE["regime"] = detect_regime()
            update_allocator()

            STATE["signals"] += 1

            await monitor()

            tokens = [f"TOKEN{i}" for i in range(20)]

            for mint in tokens:
                if len(STATE["positions"]) >= MAX_POSITIONS:
                    break

                engine = random.choices(
                    ["stable", "degen", "sniper"],
                    weights=[
                        STATE["allocator"]["stable"],
                        STATE["allocator"]["degen"],
                        STATE["allocator"]["sniper"],
                    ],
                )[0]

                alpha = random.uniform(5, 80)

                # ===== filters =====
                if engine == "stable" and alpha < 10:
                    continue
                if engine == "degen" and alpha < 30:
                    continue
                if engine == "sniper" and alpha < 50:
                    continue

                size = get_size(alpha, engine)

                price, qty = await simulate_buy(size)

                STATE["positions"].append({
                    "token": mint,
                    "entry_price": price,
                    "qty": qty,
                    "alpha": alpha,
                    "engine": engine,
                    "entry_time": time.time()
                })

                STATE["daily_trades"] += 1
                STATE["last_action"] = f"{engine}_buy"

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
