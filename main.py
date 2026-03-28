import asyncio
import random
import httpx
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
    "candidates": [],
    "last_execution": None,

    "realized_pnl": 0.0,
    "daily_pnl": 0.0,
    "daily_trades": 0,
    "last_reset": time.time(),

    "loss_streak": 0,
    "regime": "chop",

    "engine_stats": {
        "stable": {"pnl": 0.0, "trades": 0, "wins": 0, "winrate": 0.0},
        "degen": {"pnl": 0.0, "trades": 0, "wins": 0, "winrate": 0.0},
    },

    "allocator": {
        "stable_weight": 0.5,
        "degen_weight": 0.5,
    },

    "bot_version": "v8_fund_brain"
}

# ================= CONFIG =================

STOP_LOSS = -0.06
DAILY_STOP = -0.05

MAX_POSITIONS = 5
MAX_DAILY_TRADES = 30
MAX_HOLD_SECONDS = 180

GAS_COST = 0.000005

# ================= 🧠 REGIME =================

def detect_regime():
    pnl = STATE["daily_pnl"]
    loss = STATE["loss_streak"]

    if pnl > 0.01:
        return "bull"
    if loss >= 3:
        return "bear"
    return "chop"

# ================= 🧠 ALLOCATOR =================

def update_allocator():
    stable = STATE["engine_stats"]["stable"]
    degen = STATE["engine_stats"]["degen"]

    total = abs(stable["pnl"]) + abs(degen["pnl"]) + 1e-6

    stable_weight = abs(stable["pnl"]) / total
    degen_weight = abs(degen["pnl"]) / total

    if STATE["regime"] == "bull":
        degen_weight += 0.2
    elif STATE["regime"] == "bear":
        stable_weight += 0.3

    total = stable_weight + degen_weight
    STATE["allocator"]["stable_weight"] = stable_weight / total
    STATE["allocator"]["degen_weight"] = degen_weight / total

# ================= SIZE =================

def get_position_size(alpha, engine):
    base = 0.003 if engine == "stable" else 0.002

    weight = STATE["allocator"][f"{engine}_weight"]

    size = base * weight * (1 + alpha / 50)

    if STATE["loss_streak"] >= 2:
        size *= 0.5

    return round(size, 4)

# ================= EXECUTION =================

async def simulate_buy(mint, size):
    price = random.uniform(0.00001, 0.00002)

    return {
        "price": price,
        "qty": size / price
    }

async def simulate_sell(pos):
    price = pos["entry_price"] * random.uniform(0.7, 1.5)

    pnl = (price - pos["entry_price"]) * pos["token_qty"]
    pnl_pct = pnl / (pos["entry_price"] * pos["token_qty"])

    return price, pnl, pnl_pct

# ================= MONITOR =================

async def monitor_positions():
    new_positions = []

    for pos in STATE["positions"]:
        price, pnl, pnl_pct = await simulate_sell(pos)

        pos["pnl_pct"] = pnl_pct
        pos["peak"] = max(pos.get("peak", 0), pnl_pct)

        # ===== TP1 =====
        if not pos.get("tp1") and pnl_pct > 0.08:
            pos["tp1"] = True
            pos["size"] *= 0.5
            pos["token_qty"] *= 0.5

        # ===== trailing =====
        giveback = 0.06
        if pos["peak"] > 0.2:
            giveback = 0.1
        if pos["peak"] > 0.5:
            giveback = 0.2

        if pnl_pct < STOP_LOSS or (pos["peak"] > 0.08 and pos["peak"] - pnl_pct > giveback):
            STATE["closed_trades"].append(pos)
            STATE["realized_pnl"] += pnl
            STATE["daily_pnl"] += pnl

            engine = pos["engine"]
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

            await monitor_positions()

            tokens = [f"TOKEN{i}" for i in range(10)]

            for mint in tokens:
                if len(STATE["positions"]) >= MAX_POSITIONS:
                    break

                engine = "degen" if random.random() < STATE["allocator"]["degen_weight"] else "stable"

                alpha = random.uniform(5, 60)

                if engine == "stable" and alpha < 10:
                    continue
                if engine == "degen" and alpha < 30:
                    continue

                size = get_position_size(alpha, engine)

                buy = await simulate_buy(mint, size)

                STATE["positions"].append({
                    "token": mint,
                    "entry_price": buy["price"],
                    "token_qty": buy["qty"],
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
