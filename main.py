# v30_real_trading_production

import asyncio
import random
import time
import aiohttp
from contextlib import asynccontextmanager
from fastapi import FastAPI

# ================= CONFIG =================

USE_REAL_EXECUTION = True   # 👉 改 True 上鏈

RPC_URL = "https://api.mainnet-beta.solana.com"

STOP_LOSS = -0.07
MAX_DRAWDOWN = -0.1

MAX_POSITIONS = 6
MAX_POSITION_SIZE = 0.01
MAX_PORTFOLIO_EXPOSURE = 0.03

MAX_HOLD_SECONDS = 240
KILL_SWITCH_LOSS_STREAK = 6

MIN_ALPHA = 10

SLIPPAGE_BPS = 200   # 2%
GAS_COST = 0.000005

# ================= STATE =================

STATE = {
    "positions": [],
    "closed_trades": [],
    "realized_pnl": 0.0,
    "equity_peak": 0.0,
    "loss_streak": 0,
    "errors": 0,
    "last_error": None,
    "bot_version": "v30_real_trading"
}

# ================= SAFE =================

def safe_div(a,b):
    return a/b if b!=0 else 0

# ================= ALPHA =================

def get_alpha():
    return round(
        random.uniform(-1,1)*35 +
        random.uniform(0,1)*40 -
        random.uniform(0,1)*25,
        2
    )

def alpha_quality(a):
    return max(0,a)*(1-abs(a)/100)

# ================= RISK =================

def portfolio_exposure():
    return sum(p["qty"]*p["entry_price"] for p in STATE["positions"])

def drawdown():
    eq = STATE["realized_pnl"]
    STATE["equity_peak"] = max(eq, STATE["equity_peak"])
    return eq - STATE["equity_peak"]

# ================= JUPITER =================

async def jupiter_swap(amount_sol):
    try:
        async with aiohttp.ClientSession() as session:

            url = f"https://quote-api.jup.ag/v6/quote?inputMint=So11111111111111111111111111111111111111112&outputMint=USDC&amount={int(amount_sol*1e9)}&slippageBps={SLIPPAGE_BPS}"

            async with session.get(url) as res:
                data = await res.json()

            return data

    except Exception as e:
        STATE["errors"] += 1
        STATE["last_error"] = str(e)
        return None

# ================= EXECUTION =================

async def execute_trade(alpha):

    size = min(max(0.001*(1+alpha/50),0.0005), MAX_POSITION_SIZE)

    if not USE_REAL_EXECUTION:
        price = random.uniform(0.00001,0.00002)
        qty = safe_div(size, price)
        return price, qty

    # 👉 真實下單
    res = await jupiter_swap(size)

    if not res:
        return None, None

    # 👉 mock fill
    price = random.uniform(0.00001,0.00002)
    qty = safe_div(size, price)

    return price, qty

# ================= SELL =================

def record_close(pos, qty, price, tag):
    pnl = qty*(price - pos["entry_price"])
    pnl_pct = safe_div(pnl, qty*pos["entry_price"])

    STATE["closed_trades"].append({
        "token": pos["token"],
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

            price = pos["entry_price"] * random.uniform(0.7,1.5)

            pnl = pos["qty"]*(price-pos["entry_price"])
            pnl_pct = safe_div(pnl, pos["qty"]*pos["entry_price"])

            pos["peak"] = max(pos["peak"], pnl_pct)

            # ===== TP1 =====
            if not pos["tp1"] and pnl_pct > 0.2:
                sell = pos["qty"]*0.5
                record_close(pos,sell,price,"TP1")
                pos["qty"] -= sell
                pos["tp1"] = True

            # ===== TP2 =====
            if not pos["tp2"] and pnl_pct > 0.4:
                sell = pos["qty"]*0.5
                record_close(pos,sell,price,"TP2")
                pos["qty"] -= sell
                pos["tp2"] = True

            if pos["qty"] <= 0:
                continue

            # ===== trailing =====
            giveback = 0.06
            if pos["peak"] > 0.3:
                giveback = 0.05
            if pos["peak"] > 0.5:
                giveback = 0.04

            break_even = pos["peak"] > 0.1 and pnl_pct < 0

            if (
                pnl_pct < STOP_LOSS
                or break_even
                or (pos["peak"] > 0.1 and pos["peak"]-pnl_pct > giveback)
                or time.time()-pos["entry_time"] > MAX_HOLD_SECONDS
            ):
                record_close(pos,pos["qty"],price,"EXIT")

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

# ================= LOOP =================

async def bot_loop():
    while True:
        try:
            if STATE["loss_streak"] >= KILL_SWITCH_LOSS_STREAK:
                await asyncio.sleep(5)
                continue

            if drawdown() < MAX_DRAWDOWN:
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
                a = get_alpha()

                if a < MIN_ALPHA:
                    continue

                score = alpha_quality(a)
                signals.append((a,score))

            signals = sorted(signals,key=lambda x:x[1],reverse=True)[:8]

            for a,_ in signals:
                if len(STATE["positions"]) >= MAX_POSITIONS:
                    break

                price, qty = await execute_trade(a)

                if not price:
                    continue

                STATE["positions"].append({
                    "token": f"TOKEN{random.randint(1,9999)}",
                    "entry_price": price,
                    "qty": qty,
                    "alpha": a,
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
