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

    "engine_stats": {
        "stable": {"pnl": 0.0, "trades": 0, "wins": 0},
        "degen": {"pnl": 0.0, "trades": 0, "wins": 0},
    },

    "bot_version": "alpha_dual_engine_v4_tracking",
}

# ================= CONFIG =================

MAX_POSITIONS = 4
MAX_DAILY_TRADES = 20
MAX_HOLD_SECONDS = 120

STOP_LOSS = -0.10
TAKE_PROFIT = 0.20
DAILY_STOP = -0.03

GAS_COST = 0.000005

# ================= HELPERS =================

def has_position(mint):
    return any(p["token"] == mint for p in STATE["positions"])


def is_valid_mint(mint):
    return mint and len(mint) >= 32 and not any(c in mint for c in [".", "/", ":"])


def get_position_size(alpha, engine):
    if engine == "stable":
        return 0.006

    if alpha > 50:
        return 0.003
    elif alpha > 30:
        return 0.002
    else:
        return 0.001


# ================= EXECUTION =================

async def simulate_buy(mint, size):
    price = random.uniform(0.00001, 0.00002)

    result = {
        "ok": True,
        "mint": mint,
        "size": size,
        "mark_price": price,
        "fill_price": price,
        "token_qty": size / price,
        "gas_cost": GAS_COST,
        "slippage": random.uniform(0, 0.01),
        "timestamp": time.time(),
    }

    STATE["last_execution"] = result
    return result


async def simulate_sell(pos):
    price = pos["entry_price"] * random.uniform(0.7, 1.3)

    value = pos["token_qty"] * price
    entry = pos["token_qty"] * pos["entry_price"]

    pnl = value - entry
    pnl_pct = pnl / entry

    result = {
        "price": price,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "timestamp": time.time(),
    }

    STATE["last_execution"] = result
    return result


# ================= ENGINE TRACKING =================

def update_engine_stats(engine, pnl):
    s = STATE["engine_stats"][engine]
    s["trades"] += 1
    s["pnl"] += pnl

    if pnl > 0:
        s["wins"] += 1


# ================= MONITOR =================

async def monitor_positions():
    new_positions = []

    for pos in STATE["positions"]:
        r = await simulate_sell(pos)

        pos["mark_price"] = r["price"]
        pos["pnl_pct"] = r["pnl_pct"]

        exit_reason = None

        if r["pnl_pct"] < STOP_LOSS:
            exit_reason = "stop_loss"

        elif r["pnl_pct"] > TAKE_PROFIT:
            exit_reason = "take_profit"

        elif time.time() - pos["entry_time"] > MAX_HOLD_SECONDS:
            exit_reason = "timeout"

        if exit_reason:
            pnl = r["pnl"] - GAS_COST

            closed = dict(pos)
            closed.update({
                "exit_price": r["price"],
                "exit_time": r["timestamp"],
                "exit_reason": exit_reason,
                "realized_pnl": pnl,
            })

            STATE["closed_trades"].append(closed)

            STATE["realized_pnl"] += pnl
            STATE["daily_pnl"] += pnl

            # ✅ engine stats
            update_engine_stats(pos["engine"], pnl)

            # ✅ loss streak
            if pnl < 0:
                STATE["loss_streak"] += 1
            else:
                STATE["loss_streak"] = 0

            continue

        new_positions.append(pos)

    STATE["positions"] = new_positions


# ================= ALPHA =================

async def real_alpha():
    return round(random.uniform(-10, 60), 2)


# ================= SCAN =================

async def scan_tokens():
    tokens = [f"TOKEN_{i}" for i in range(10)]
    STATE["candidate_count"] = len(tokens)
    return tokens


# ================= BOT LOOP =================

async def bot_loop():
    while True:
        try:
            if STATE["daily_pnl"] < DAILY_STOP:
                STATE["last_action"] = "daily_stop"
                await asyncio.sleep(5)
                continue

            if STATE["loss_streak"] >= 3:
                STATE["last_action"] = "cooldown"
                await asyncio.sleep(5)
                continue

            STATE["signals"] += 1
            tokens = await scan_tokens()

            await monitor_positions()

            for mint in tokens:

                if STATE["daily_trades"] >= MAX_DAILY_TRADES:
                    break

                if len(STATE["positions"]) >= MAX_POSITIONS:
                    break

                if has_position(mint):
                    continue

                if not is_valid_mint(mint):
                    continue

                alpha = await real_alpha()

                if abs(alpha) > 120:
                    continue

                engine = "stable" if alpha > 20 else "degen"

                if engine == "stable" and alpha < 15:
                    continue

                if engine == "degen" and alpha < 25:
                    continue

                size = get_position_size(alpha, engine)

                buy = await simulate_buy(mint, size)

                STATE["positions"].append({
                    "token": mint,
                    "alpha": alpha,
                    "size": size,
                    "entry_price": buy["fill_price"],
                    "mark_price": buy["mark_price"],
                    "token_qty": buy["token_qty"],
                    "entry_time": time.time(),
                    "pnl_pct": 0,
                    "engine": engine,
                })

                STATE["daily_trades"] += 1
                STATE["last_action"] = f"{engine}_buy"

        except Exception as e:
            STATE["errors"] += 1
            STATE["last_action"] = str(e)

        await asyncio.sleep(2)


# ================= FASTAPI =================

bot_task = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot_task
    bot_task = asyncio.create_task(bot_loop())
    yield
    bot_task.cancel()

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def root():
    return {"ok": True}

@app.get("/metrics")
async def metrics():
    stats = {}

    for k, v in STATE["engine_stats"].items():
        trades = v["trades"]
        wins = v["wins"]
        winrate = (wins / trades) if trades else 0

        stats[k] = {
            "pnl": v["pnl"],
            "trades": trades,
            "winrate": round(winrate, 2),
        }

    return {
        "positions": STATE["positions"],
        "closed_trades": STATE["closed_trades"],
        "signals": STATE["signals"],
        "errors": STATE["errors"],
        "last_action": STATE["last_action"],
        "realized_pnl": STATE["realized_pnl"],
        "daily_pnl": STATE["daily_pnl"],
        "engine_stats": stats,
        "bot_version": STATE["bot_version"],
    }
