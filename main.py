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
MAX_POSITION_PER_ENGINE = 3
MAX_POSITION_SIZE = 0.01
MAX_PORTFOLIO_EXPOSURE = 0.03

MAX_HOLD_SECONDS = 240
KILL_SWITCH_LOSS_STREAK = 6
MAX_DRAWDOWN = -0.1

MIN_ALPHA_TO_TRADE = 8

# ================= STATE =================

STATE = {
    "positions": [],
    "closed_trades": [],
    "trade_log": [],

    "realized_pnl": 0.0,
    "daily_pnl": 0.0,

    "loss_streak": 0,
    "equity_peak": 0.0,

    "regime": "chop",

    "engine_stats": {
        "stable": {"pnl": 0, "trades": 0, "wins": 0},
        "degen": {"pnl": 0, "trades": 0, "wins": 0},
        "sniper": {"pnl": 0, "trades": 0, "wins": 0},
    },

    "alpha_memory": {
        "stable": [],
        "degen": [],
        "sniper": []
    },

    "allocator": {
        "stable": 0.4,
        "degen": 0.4,
        "sniper": 0.2,
    },

    "last_action": None,
    "errors": 0,

    "bot_version": "v23_warfare_final"
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

# ================= AI QUALITY =================

def alpha_quality(alpha):
    return max(0, alpha) * (1 - abs(alpha)/100)

# ================= MEMORY =================

def update_alpha_memory(engine, alpha, pnl):
    mem = STATE["alpha_memory"][engine]
    mem.append((alpha, pnl))
    if len(mem) > 100:
        mem.pop(0)

def get_alpha_edge(engine, alpha):
    mem = STATE["alpha_memory"][engine]
    if not mem:
        return 1.0

    similar = [p for a, p in mem if abs(a - alpha) < 10]
    if not similar:
        return 1.0

    avg = sum(similar) / len(similar)
    return max(0.5, min(2.0, 1 + avg * 6))

# ================= ENGINE SCORE =================

def engine_score(engine):
    s = STATE["engine_stats"][engine]

    if s["trades"] < 5:
        return 1.0

    winrate = safe_div(s["wins"], s["trades"])
    pnl = s["pnl"]

    decay = 1 / (1 + abs(pnl))

    score = (0.6 * pnl + 0.4 * winrate) * decay

    return max(0.1, min(3.0, score))

# ================= ALLOCATOR =================

def update_allocator():
    weights = {e: engine_score(e) for e in ["stable","degen","sniper"]}

    total = sum(weights.values()) + 1e-6
    weights = {k: v/total for k,v in weights.items()}

    if STATE["regime"] == "bull":
        weights["degen"] += 0.2
        weights["sniper"] += 0.1
    elif STATE["regime"] == "bear":
        weights["stable"] += 0.3

    total = sum(weights.values())
    STATE["allocator"] = {k: max(v/total,0.05) for k,v in weights.items()}

# ================= SIZE =================

def get_size(alpha, engine):
    base = {"stable":0.003,"degen":0.002,"sniper":0.0015}[engine]

    size = base * STATE["allocator"][engine]
    size *= (1 + alpha/50)
    size *= get_alpha_edge(engine, alpha)

    if STATE["loss_streak"] >= 2:
        size *= 0.5

    return min(max(0.0005, round(size,4)), MAX_POSITION_SIZE)

# ================= EXEC =================

async def execute_trade(engine, alpha, size):
    if size <= 0:
        return None

    price = random.uniform(0.00001,0.00002)
    qty = safe_div(size, price)

    trade = {
        "engine":engine,
        "alpha":alpha,
        "price":price,
        "qty":qty,
        "time":time.time()
    }

    STATE["trade_log"].append(trade)
    return trade

# ================= RISK =================

def can_open(engine):
    return sum(1 for p in STATE["positions"] if p["engine"]==engine) < MAX_POSITION_PER_ENGINE

def portfolio_exposure():
    return sum(p["qty"]*p["entry_price"] for p in STATE["positions"])

def check_drawdown():
    equity = STATE["realized_pnl"]

    STATE["equity_peak"] = max(STATE["equity_peak"], equity)

    drawdown = equity - STATE["equity_peak"]

    return drawdown

# ================= MONITOR v29 =================

async def monitor():
    new_positions = []

    for pos in STATE["positions"]:
        pnl, pnl_pct = await simulate_sell(pos)

        # ===== 更新 peak =====
        pos["peak"] = max(pos.get("peak", 0), pnl_pct)

        # ===== 分段止盈 =====
        if not pos.get("tp1") and pnl_pct > 0.15:
            pos["tp1"] = True
            pos["qty"] *= 0.5

        if not pos.get("tp2") and pnl_pct > 0.30:
            pos["tp2"] = True
            pos["qty"] *= 0.75

        # ===== 動態 trailing =====
        giveback = 0.05 + pos["alpha"] / 200

        if pos["peak"] > 0.3:
            giveback += 0.05
        if pos["peak"] > 0.6:
            giveback += 0.1

        # ===== Break-even 保護 =====
        break_even = pos["peak"] > 0.1 and pnl_pct < 0

        # ===== 出場條件 =====
        should_close = (
            pnl_pct < STOP_LOSS
            or break_even
            or (pos["peak"] > 0.08 and pos["peak"] - pnl_pct > giveback)
            or time.time() - pos["entry_time"] > MAX_HOLD_SECONDS
        )

        if should_close:
            engine = pos["engine"]

            STATE["closed_trades"].append({
                **pos,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "exit_time": time.time()
            })

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

    # 🔥 只保留最強倉（基金邏輯）
    STATE["positions"] = sorted(
        new_positions,
        key=position_score,
        reverse=True
    )[:MAX_POSITIONS]


# ================= LOOP =================

async def bot_loop():
    while True:
        try:
            # ===== HARD RISK =====
            if STATE["loss_streak"] >= KILL_SWITCH_LOSS_STREAK:
                STATE["last_action"]="KILL_SWITCH"
                await asyncio.sleep(10)
                continue

            if STATE["daily_pnl"] < DAILY_STOP:
                await asyncio.sleep(5)
                continue

            if check_drawdown() < MAX_DRAWDOWN:
                STATE["last_action"]="DRAWDOWN_STOP"
                await asyncio.sleep(10)
                continue

            if portfolio_exposure() > MAX_PORTFOLIO_EXPOSURE:
                await asyncio.sleep(3)
                continue

            STATE["regime"] = detect_regime()
            update_allocator()

            await monitor()

            if random.random() > 0.6:
                await asyncio.sleep(2)
                continue

            # ===== AI SELECT =====
            candidates = []

            for _ in range(40):
                alpha = get_real_alpha()
                score = alpha_quality(alpha)

                if alpha < MIN_ALPHA_TO_TRADE:
                    continue

                candidates.append((alpha,score))

            candidates = sorted(candidates,key=lambda x:x[1],reverse=True)[:12]

            for alpha,_ in candidates:
                if len(STATE["positions"])>=MAX_POSITIONS:
                    break

                engine = random.choices(
                    ["stable","degen","sniper"],
                    weights=list(STATE["allocator"].values())
                )[0]

                if not can_open(engine):
                    continue

                if engine=="stable" and alpha<8:
                    continue
                if engine=="degen" and alpha<25:
                    continue
                if engine=="sniper" and alpha<45:
                    continue

                size = get_size(alpha,engine)

                trade = await execute_trade(engine,alpha,size)
                if not trade:
                    continue

                STATE["positions"].append({
                    "token":f"TOKEN{random.randint(1,1000)}",
                    "entry_price":trade["price"],
                    "qty":trade["qty"],
                    "alpha":alpha,
                    "engine":engine,
                    "entry_time":time.time()
                })

        except Exception as e:
            STATE["errors"] += 1

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
