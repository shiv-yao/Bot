# v31.5_real_stable (Railway READY)

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

USE_REAL_EXECUTION = True

RPC_URL = "https://api.mainnet-beta.solana.com"
JUP_API = "https://lite-api.jup.ag"   # 🔥 FIXED

SLIPPAGE_BPS = 200

MAX_POSITIONS = 5
MAX_POSITION_SIZE = 0.01

STOP_LOSS = -0.07

# ================= PRIVATE KEY =================

PRIVATE_KEY = os.getenv("PRIVATE_KEY", "").strip()

if not PRIVATE_KEY:
    raise RuntimeError("PRIVATE_KEY not set")

try:
    if PRIVATE_KEY.startswith("["):
        keypair = Keypair.from_bytes(bytes(eval(PRIVATE_KEY)))
    elif "," in PRIVATE_KEY:
        keypair = Keypair.from_bytes(
            bytes(int(x) for x in PRIVATE_KEY.split(","))
        )
    else:
        keypair = Keypair.from_base58_string(PRIVATE_KEY)
except Exception as e:
    raise RuntimeError(f"PRIVATE_KEY format error: {e}")

# ================= GLOBAL =================

SESSION = None

# ================= STATE =================

STATE = {
    "positions": [],
    "closed_trades": [],
    "realized_pnl": 0.0,
    "errors": 0,
    "last_error": None,
    "bot_version": "v31.5_real_stable"
}

# ================= UTILS =================

async def safe_get(url):
    for _ in range(3):
        try:
            async with SESSION.get(url, timeout=5) as res:
                return await res.json()
        except Exception:
            await asyncio.sleep(0.3)
    return None

async def safe_post(url, json_data):
    for _ in range(3):
        try:
            async with SESSION.post(url, json=json_data, timeout=5) as res:
                return await res.json()
        except Exception:
            await asyncio.sleep(0.3)
    return None

# ================= ALPHA =================

def get_alpha():
    return random.uniform(10, 80)

# ================= JUPITER =================

async def get_quote(amount):
    url = (
        f"{JUP_API}/v6/quote"
        f"?inputMint=So11111111111111111111111111111111111111112"
        f"&outputMint=EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
        f"&amount={int(amount*1e9)}"
        f"&slippageBps={SLIPPAGE_BPS}"
    )
    return await safe_get(url)

async def get_swap_tx(route):
    return await safe_post(
        f"{JUP_API}/v6/swap",
        {
            "quoteResponse": route,
            "userPublicKey": str(keypair.pubkey()),
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": 5000
        }
    )

# ================= EXECUTION =================

async def send_tx(tx_base64):
    try:
        tx_bytes = base64.b64decode(tx_base64)
        tx = VersionedTransaction.from_bytes(tx_bytes)

        tx.sign([keypair])

        raw_tx = base64.b64encode(bytes(tx)).decode()

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendTransaction",
            "params": [raw_tx, {"skipPreflight": True}]
        }

        res = await safe_post(RPC_URL, payload)
        return res

    except Exception as e:
        STATE["errors"] += 1
        STATE["last_error"] = str(e)
        return None

async def execute_real_trade(amount):
    quote = await get_quote(amount)

    if not quote or "data" not in quote:
        return None

    route = quote["data"][0]

    swap = await get_swap_tx(route)

    if not swap or "swapTransaction" not in swap:
        return None

    tx = swap["swapTransaction"]

    for _ in range(2):
        result = await send_tx(tx)

        if result and "result" in result:
            return {
                "price": float(route["outAmount"]) / float(route["inAmount"])
            }

    return None

# ================= EXEC WRAPPER =================

async def execute_trade(alpha):
    size = min(0.002 * (1 + alpha/50), MAX_POSITION_SIZE)

    if not USE_REAL_EXECUTION:
        price = random.uniform(0.00001, 0.00002)
        return price, size / price

    res = await execute_real_trade(size)

    if not res:
        return None, None

    price = res["price"]
    return price, size / price

# ================= MONITOR =================

async def monitor():
    new_positions = []

    for pos in STATE["positions"]:
        price = pos["entry_price"] * random.uniform(0.7, 1.5)

        pnl = pos["qty"] * (price - pos["entry_price"])
        pnl_pct = pnl / (pos["qty"] * pos["entry_price"])

        if pnl_pct < STOP_LOSS:
            STATE["closed_trades"].append({
                **pos,
                "exit_price": price,
                "pnl": pnl
            })
            STATE["realized_pnl"] += pnl
            continue

        new_positions.append(pos)

    STATE["positions"] = new_positions

# ================= LOOP =================

async def bot_loop():
    while True:
        try:
            await monitor()

            for _ in range(3):
                if len(STATE["positions"]) >= MAX_POSITIONS:
                    break

                alpha = get_alpha()

                price, qty = await execute_trade(alpha)

                if not price:
                    continue

                STATE["positions"].append({
                    "token": f"TOKEN{random.randint(1,9999)}",
                    "entry_price": price,
                    "qty": qty,
                    "alpha": alpha,
                    "entry_time": time.time()
                })

        except Exception as e:
            STATE["errors"] += 1
            STATE["last_error"] = str(e)

        await asyncio.sleep(2)

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
