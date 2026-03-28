# v47_fund_accounting_full

import asyncio
import random
import time
import aiohttp
import base64
import os

from contextlib import asynccontextmanager
from fastapi import FastAPI
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

# ================= CONFIG =================

JITO_ENDPOINTS = [
    "https://mainnet.block-engine.jito.wtf/api/v1/bundles",
    "https://ny.block-engine.jito.wtf/api/v1/bundles"
]

JUP_API = "https://lite-api.jup.ag"

MAX_POSITIONS = 5
MAX_POSITION_SIZE = 0.01

STOP_LOSS = -0.07
TAKE_PROFIT = 0.3

KILL_SWITCH = 5
BASE_SLIPPAGE = 150

# ================= KEY =================

PRIVATE_KEY = os.getenv("PRIVATE_KEY", "").strip()

if not PRIVATE_KEY:
    raise RuntimeError("PRIVATE_KEY not set")

if PRIVATE_KEY.startswith("["):
    keypair = Keypair.from_bytes(bytes(eval(PRIVATE_KEY)))
elif "," in PRIVATE_KEY:
    keypair = Keypair.from_bytes(bytes(int(x) for x in PRIVATE_KEY.split(",")))
else:
    keypair = Keypair.from_base58_string(PRIVATE_KEY)

# ================= STATE =================

SESSION = None

STATE = {
    "positions": [],
    "trade_log": [],
    "equity_curve": [],
    "balance": 1.0,
    "peak_balance": 1.0,
    "max_drawdown": 0.0,

    "flow_history": [],
    "realized_pnl": 0.0,
    "loss_streak": 0,

    "errors": 0,
    "last_error": None,
    "kill": False,
    "last_heartbeat": time.time(),

    "bot_version": "v47_fund_accounting"
}

# ================= SAFE =================

async def safe_get(url):
    try:
        async with SESSION.get(url, timeout=4) as res:
            return await res.json()
    except Exception as e:
        STATE["errors"] += 1
        STATE["last_error"] = str(e)
        return None

async def safe_post(url, data):
    try:
        async with SESSION.post(url, json=data, timeout=4) as res:
            return await res.json()
    except Exception as e:
        STATE["errors"] += 1
        STATE["last_error"] = str(e)
        return None

# ================= FLOW =================

async def update_flow():
    flow = random.uniform(0,1)
    STATE["flow_history"].append(flow)

    if len(STATE["flow_history"]) > 20:
        STATE["flow_history"].pop(0)

def flow_acceleration():
    if len(STATE["flow_history"]) < 2:
        return 0
    return STATE["flow_history"][-1] - STATE["flow_history"][-2]

# ================= ALPHA =================

async def compute_alpha():
    flow = random.uniform(0,1)

    accel = flow_acceleration()
    mem = random.uniform(0,1)
    launch = random.random() < 0.1

    return flow*50 + accel*80 + mem*60 + (80 if launch else 0)

# ================= ACCOUNTING =================

def record_trade(entry, exit, size, alpha):
    pnl = (exit - entry) * size
    pnl_pct = pnl / (entry * size)

    STATE["realized_pnl"] += pnl
    STATE["balance"] += pnl

    STATE["equity_curve"].append({
        "time": time.time(),
        "balance": STATE["balance"]
    })

    STATE["peak_balance"] = max(STATE["peak_balance"], STATE["balance"])

    dd = (STATE["peak_balance"] - STATE["balance"]) / STATE["peak_balance"]
    STATE["max_drawdown"] = max(STATE["max_drawdown"], dd)

    STATE["trade_log"].append({
        "entry": entry,
        "exit": exit,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "alpha": alpha,
        "time": time.time()
    })

    if pnl > 0:
        STATE["loss_streak"] = 0
    else:
        STATE["loss_streak"] += 1

# ================= POSITION UPDATE =================

async def update_positions():
    new_positions = []

    for pos in STATE["positions"]:
        price = pos["entry_price"] * random.uniform(0.7, 1.5)

        pnl_pct = (price - pos["entry_price"]) / pos["entry_price"]

        if pnl_pct < STOP_LOSS or pnl_pct > TAKE_PROFIT:
            record_trade(
                pos["entry_price"],
                price,
                1,
                pos["alpha"]
            )
            continue

        new_positions.append(pos)

    STATE["positions"] = new_positions

# ================= JUP =================

async def get_quote(amount, slippage):
    return await safe_get(
        f"{JUP_API}/v6/quote"
        f"?inputMint=So11111111111111111111111111111111111111112"
        f"&outputMint=EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
        f"&amount={int(amount*1e9)}"
        f"&slippageBps={slippage}"
    )

async def get_swap(route):
    return await safe_post(
        f"{JUP_API}/v6/swap",
        {
            "quoteResponse": route,
            "userPublicKey": str(keypair.pubkey()),
            "wrapAndUnwrapSol": True
        }
    )

# ================= JITO =================

async def send_bundle_multi(tx):
    bundle = {
        "jsonrpc":"2.0",
        "id":1,
        "method":"sendBundle",
        "params":[{"transactions":[tx],"encoding":"base64"}]
    }

    results = await asyncio.gather(
        *[safe_post(url, bundle) for url in JITO_ENDPOINTS]
    )

    for r in results:
        if r and "result" in r:
            return r["result"]

    return None

# ================= EXEC =================

async def execute_real(amount, alpha):
    slippage = BASE_SLIPPAGE + int(alpha*2)

    quote = await get_quote(amount, slippage)
    if not quote or "data" not in quote or not quote["data"]:
        return None

    route = quote["data"][0]

    swap = await get_swap(route)
    if not swap or "swapTransaction" not in swap:
        return None

    tx = VersionedTransaction.from_bytes(
        base64.b64decode(swap["swapTransaction"])
    )
    tx.sign([keypair])

    raw = base64.b64encode(bytes(tx)).decode()

    sig = await send_bundle_multi(raw)

    if sig:
        return float(route["outAmount"]) / float(route["inAmount"])

    return None

# ================= LOOP =================

async def bot_loop():
    while True:
        try:
            if STATE["kill"]:
                await asyncio.sleep(2)
                continue

            if STATE["loss_streak"] >= KILL_SWITCH:
                await asyncio.sleep(5)
                continue

            await update_flow()
            await update_positions()

            for _ in range(5):
                if len(STATE["positions"]) >= MAX_POSITIONS:
                    break

                alpha = await compute_alpha()

                if alpha < 50:
                    continue

                size = min(0.002*(1+alpha/50), MAX_POSITION_SIZE)

                price = await execute_real(size, alpha)

                if not price:
                    continue

                STATE["positions"].append({
                    "token": f"TOKEN{random.randint(1,9999)}",
                    "entry_price": price,
                    "alpha": alpha,
                    "time": time.time()
                })

        except Exception as e:
            STATE["errors"] += 1
            STATE["last_error"] = str(e)

        STATE["last_heartbeat"] = time.time()

        await asyncio.sleep(1)

# ================= API =================

bot_task = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot_task, SESSION

    SESSION = aiohttp.ClientSession()
    bot_task = asyncio.create_task(bot_loop())

    yield

    await SESSION.close()
    bot_task.cancel()

app = FastAPI(lifespan=lifespan)

@app.get("/")
def root():
    return {"ok": True}

@app.get("/metrics")
def metrics():
    return STATE

@app.get("/analytics")
def analytics():
    trades = STATE["trade_log"]

    if not trades:
        return {"msg": "no trades"}

    wins = [t for t in trades if t["pnl"] > 0]

    return {
        "trades": len(trades),
        "winrate": len(wins)/len(trades),
        "pnl": STATE["realized_pnl"],
        "balance": STATE["balance"],
        "max_drawdown": STATE["max_drawdown"]
    }

@app.post("/kill")
def kill():
    STATE["kill"] = True
    return {"ok": True}

@app.post("/resume")
def resume():
    STATE["kill"] = False
    return {"ok": True}
