# v29.1_stable_production

import asyncio
import random
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI

# ================= CONFIG =================

STOP_LOSS = -0.07
DAILY_STOP = -0.06
MAX_DRAWDOWN = -0.1

MAX_POSITIONS = 6
MAX_POSITION_PER_ENGINE = 3
MAX_POSITION_SIZE = 0.01
MAX_PORTFOLIO_EXPOSURE = 0.03

MAX_HOLD_SECONDS = 240
KILL_SWITCH_LOSS_STREAK = 6

MIN_ALPHA_TO_TRADE = 8

# ================= STATE =================

STATE = {
    "positions": [],
    "closed_trades": [],
    "trade_log": [],

    "realized_pnl": 0.0,
    "daily_pnl": 0.0,
    "equity_peak": 0.0,

    "loss_streak": 0,

    "last_action": None,
    "last_error": None,
    "errors": 0,

    "bot_version": "v29.1_stable"
}

# ================= SAFE =================

def safe_div(a, b):
    return a / b if b != 0 else 0

# ================= ALPHA =================

def get_real_alpha():
    return round(
        random.uniform(-1,1)*35 +
        random.uniform(0,1)*40 -
        random.uniform(0,1)*25,
        2
    )

def alpha_quality(alpha):
    return max(0, alpha) * (1 - abs(alpha)/100)

# ================= RISK =================

def portfolio_exposure():
    return sum(p.get("qty",0)*p.get("entry_price",0) for p in STATE["positions"])

def check_drawdown():
    eq = STATE["realized_pnl"]
    STATE["equity_peak"] = max(STATE["equity_peak"], eq)
    return eq - STATE["equity_peak"]

# ================= SELL =================

async def simulate_sell(pos):
    try:
        price = pos.get("entry_price",0) * random.uniform(0.6,1.6)

        value = pos.get("qty",0) * price
        entry = pos.get("qty",0) * pos.get("entry_price",0)

        pnl = value - entry
        pnl_pct = safe_div(pnl, entry)

        return pnl, pnl_pct
    except:
        return 0, 0

# ================= MONITOR =================

async def monitor():
    new_positions = []

    for pos in STATE["positions"]:
        try:
            # 🔥 自動補欄位（關鍵）
            pos.setdefault("peak", 0)
            pos.setdefault("tp1", False)
            pos.setdefault("tp2", False)

            pnl, pnl_pct = await simulate_sell(pos)

            pos["peak"] = max(pos["peak"], pnl_pct)

            # ===== TP =====
            if not pos["tp1"] and pnl_pct > 0.15:
                pos["tp1"] = True
                pos["qty"] *= 0.5

            if not pos["tp2"] and pnl_pct > 0.30:
                pos["tp2"] = True
                pos["qty"] *= 0.75

            # ===== trailing =====
            giveback = 0.05 + pos.get("alpha",0)/200

            if pos["peak"] > 0.3:
                giveback += 0.05
            if pos["peak"] > 0.6:
                giveback += 0.1

            break_even = pos["peak"] > 0.1 and pnl_pct < 0

            should_close = (
                pnl_pct < STOP_LOSS
                or break_even
                or (pos["peak"] > 0.08 and pos["peak"] - pnl_pct > giveback)
                or time.time() - pos.get("entry_time",0) > MAX_HOLD_SECONDS
            )

            if should_close:
                STATE["closed_trades"].append({
                    **pos,
                    "pnl": pnl,
                    "pnl_pct": pnl_pct,
                    "exit_time": time.time()
                })

                STATE["realized_pnl"] += pnl

                if pnl > 0:
                    STATE["loss_streak"] = 0
                else:
                    STATE["loss_streak"] += 1

                continue

            new_positions.append(pos)

        except Exception as e:
            STATE["errors"] += 1
            STATE["last_error"] = str(e)
            print("MONITOR ERROR:", e)

    STATE["positions"] = new_positions[:MAX_POSITIONS]

# ================= EXEC =================

async def execute_trade(alpha):
    price = random.uniform(0.00001,0.00002)
    size = min(max(0.001*(1+alpha/50),0.0005), MAX_POSITION_SIZE)
    qty = safe_div(size, price)

    return {
        "price": price,
        "qty": qty
    }

# ================= LOOP =================

async def bot_loop():
    while True:
        try:
            if STATE["loss_streak"] >= KILL_SWITCH_LOSS_STREAK:
                await asyncio.sleep(5)
                continue

            if check_drawdown() < MAX_DRAWDOWN:
                await asyncio.sleep(5)
                continue

            if portfolio_exposure() > MAX_PORTFOLIO_EXPOSURE:
                await asyncio.sleep(3)
                continue

            await monitor()

            if random.random() > 0.6:
                await asyncio.sleep(2)
                continue

            # ===== SIGNAL =====
            signals = []

            for _ in range(30):
                alpha = get_real_alpha()

                if alpha < MIN_ALPHA_TO_TRADE:
                    continue

                score = alpha_quality(alpha)
                signals.append((alpha, score))

            signals = sorted(signals, key=lambda x: x[1], reverse=True)[:8]

            for alpha, _ in signals:
                if len(STATE["positions"]) >= MAX_POSITIONS:
                    break

                trade = await execute_trade(alpha)

                STATE["positions"].append({
                    "token": f"TOKEN{random.randint(1,9999)}",
                    "entry_price": trade["price"],
                    "qty": trade["qty"],
                    "alpha": alpha,
                    "entry_time": time.time(),

                    # 🔥 永遠初始化
                    "peak": 0,
                    "tp1": False,
                    "tp2": False
                })

        except Exception as e:
            STATE["errors"] += 1
            STATE["last_error"] = str(e)
            print("LOOP ERROR:", e)

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
