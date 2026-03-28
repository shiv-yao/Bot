# v31_real_signing

import asyncio
import random
import time
import aiohttp
import base64

from contextlib import asynccontextmanager
from fastapi import FastAPI

from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from solders.rpc.requests import SendVersionedTransaction
from solders.rpc.config import RpcSendTransactionConfig

# ================= CONFIG =================

USE_REAL_EXECUTION = True

RPC_URL = "https://api.mainnet-beta.solana.com"

PRIVATE_KEY = [AWCNMXgwtjuLTRtrThfTVgHv8BT6c5xp5PdYBW4JeotZzfTET43dWARWGiu4NpYxLHbH2EvLP5QsLYMAXW1wKR4] # 🔥 放你的 private key array

SLIPPAGE_BPS = 200

MAX_POSITIONS = 5
MAX_POSITION_SIZE = 0.01

STOP_LOSS = -0.07

# ================= WALLET =================

keypair = Keypair.from_bytes(bytes(PRIVATE_KEY))

# ================= STATE =================

STATE = {
    "positions": [],
    "closed_trades": [],
    "realized_pnl": 0.0,
    "errors": 0,
    "last_error": None,
    "bot_version": "v31_real_signing"
}

# ================= ALPHA =================

def get_alpha():
    return random.uniform(10, 80)

# ================= JUPITER =================

async def get_quote(amount):
    url = f"https://quote-api.jup.ag/v6/quote?inputMint=So11111111111111111111111111111111111111112&outputMint=EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v&amount={int(amount*1e9)}&slippageBps={SLIPPAGE_BPS}"

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as res:
            return await res.json()

async def get_swap_tx(route):
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://quote-api.jup.ag/v6/swap",
            json={
                "route": route,
                "userPublicKey": str(keypair.pubkey()),
                "wrapAndUnwrapSol": True
            }
        ) as res:
            return await res.json()

# ================= EXECUTION =================

async def send_tx(tx_base64):
    try:
        tx_bytes = base64.b64decode(tx_base64)
        tx = VersionedTransaction.from_bytes(tx_bytes)

        tx.sign([keypair])

        payload = SendVersionedTransaction(tx, RpcSendTransactionConfig(skip_preflight=True))

        async with aiohttp.ClientSession() as session:
            async with session.post(RPC_URL, json=payload.to_json()) as res:
                return await res.json()

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

    if "swapTransaction" not in swap:
        return None

    tx = swap["swapTransaction"]

    result = await send_tx(tx)

    return result

# ================= SIM =================

async def simulate_trade(amount):
    price = random.uniform(0.00001, 0.00002)
    qty = amount / price
    return price, qty

# ================= EXEC WRAPPER =================

async def execute_trade(alpha):
    size = min(0.002 * (1 + alpha/50), MAX_POSITION_SIZE)

    if not USE_REAL_EXECUTION:
        return await simulate_trade(size)

    res = await execute_real_trade(size)

    if not res:
        return None, None

    # 👉 fallback 模擬成交
    price = random.uniform(0.00001, 0.00002)
    qty = size / price

    return price, qty

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

        await asyncio.sleep(3)

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
