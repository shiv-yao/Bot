# v29.3_profit_optimized

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

    "last_error": None,
    "errors": 0,

    "bot_version": "v29.3_profit_optimized"
}

# ================= SAFE =================

def safe_div(a,b):
    return a/b if b!=0 else 0

# ================= ALPHA =================

def get_real_alpha():
    return round(
        random.uniform(-1,1)*35 +
        random.uniform(0,1)*40 -
        random.uniform(0,1)*25,
        2
    )

def alpha_quality(alpha):
    return max(0, alpha)*(1-abs(alpha)/100)

def alpha_filter(alpha):
    return 15 < alpha < 80   # 🔥 關鍵優化

# ================= RISK =================

def portfolio_exposure():
    return sum(p.get("qty",0)*p.get("entry_price",0) for p in STATE["positions"])

def check_drawdown():
    eq = STATE["realized_pnl"]
    STATE["equity_peak"] = max(STATE["equity_peak"], eq)
    return eq - STATE["equity_peak"]

# ================= SELL =================

async def simulate_sell(pos):
    price = pos["entry_price"] * random.uniform(0.7,1.5)

    value = pos["qty"] * price
    entry = pos["qty"] * pos["entry_price"]

    pnl = value - entry
    pnl_pct = safe_div(pnl, entry)

    return pnl, pnl_pct, price

# ================= PARTIAL CLOSE =================

def record_close(pos, qty, price, tag):
    pnl = qty * (price - pos["entry_price"])
    pnl_pct = safe_div(pnl, qty * pos["entry_price"])

    STATE["closed_trades"].append({
        "token": pos["token"],
        "engine": pos["engine"],
        "alpha": pos["alpha"],
        "qty": qty,
        "entry_price": pos["entry_price"],
        "exit_price": price,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "type": tag,
        "time": time.time()
    })

    STATE["realized_pnl"] += pnl

# ================= MONITOR =================

async def monitor():
    new_positions = []

    for pos in STATE["positions"]:
        try:
            pos.setdefault("peak",0)
            pos.setdefault("tp1",False)
            pos.setdefault("tp2",False)
            pos.setdefault("original_qty",pos["qty"])

            pnl, pnl_pct, price = await simulate_sell(pos)

            pos["peak"] = max(pos["peak"], pnl_pct)

            # ===== TP1 =====
if not pos["tp1"] and pnl_pct > 0.18:
    sell_qty = pos["qty"] * 0.5
    record_close(pos, sell_qty, price, "TP1")
    pos["qty"] -= sell_qty
    pos["tp1"] = True

# ===== TP2 =====
if not pos["tp2"] and pnl_pct > 0.35:
    sell_qty = pos["qty"] * 0.5
    record_close(pos, sell_qty, price, "TP2")
    pos["qty"] -= sell_qty
    pos["tp2"] = True

# ===== trailing =====
giveback = 0.06

if pos["peak"] > 0.3:
    giveback = 0.05
if pos["peak"] > 0.5:
    giveback = 0.045

# ===== break-even =====
break_even = pos["peak"] > 0.10 and pnl_pct < 0

            should_close = (
                pnl_pct < STOP_LOSS
                or break_even
                or (pos["peak"] > 0.05 and pos["peak"] - pnl_pct > giveback)
                or time.time() - pos["entry_time"] > MAX_HOLD_SECONDS
            )

            if should_close:
                record_close(pos, pos["qty"], price, "FINAL_EXIT")

                if pnl > 0:
                    STATE["loss_streak"] = 0
                else:
                    STATE["loss_streak"] += 1

                continue

            new_positions.append(pos)

        except Exception as e:
            STATE["errors"] += 1
            STATE["last_error"] = str(e)

    STATE["positions"] = new_positions[:MAX_POSITIONS]

# ================= EXEC =================

async def execute_trade(alpha):
    price = random.uniform(0.00001,0.00002)

    size = min(max(0.001*(1+alpha/50),0.0005), MAX_POSITION_SIZE)
    size *= 0.8  # 🔥 降風險

    qty = safe_div(size, price)

    return price, qty

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

            signals = []

            for _ in range(30):
                alpha = get_real_alpha()

                if not alpha_filter(alpha):
                    continue

                score = alpha_quality(alpha)
                signals.append((alpha, score))

            signals = sorted(signals, key=lambda x:x[1], reverse=True)[:8]

            for alpha,_ in signals:
                if len(STATE["positions"]) >= MAX_POSITIONS:
                    break

                price, qty = await execute_trade(alpha)

                STATE["positions"].append({
                    "token": f"TOKEN{random.randint(1,9999)}",
                    "entry_price": price,
                    "qty": qty,
                    "original_qty": qty,
                    "alpha": alpha,
                    "engine": "core",
                    "entry_time": time.time(),
                    "peak": 0,
                    "tp1": False,
                    "tp2": False
                })

        except Exception as e:
            STATE["errors"] += 1
            STATE["last_error"] = str(e)

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
