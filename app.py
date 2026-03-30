# ================= v1321 REAL ON-CHAIN SNIPER =================
import os
import json
import base64
import asyncio
import random
import httpx
from collections import defaultdict

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from solana.rpc.async_api import AsyncClient

from state import engine

# ================= CONFIG =================
RPCS = [
    os.getenv("RPC_1", "https://api.mainnet-beta.solana.com"),
    os.getenv("RPC_2", "https://rpc.ankr.com/solana"),
]

PRIVATE_KEY = os.getenv("PRIVATE_KEY")  # base58
SLIPPAGE_BPS = int(os.getenv("SLIPPAGE_BPS", "300"))
BASE_SIZE = float(os.getenv("BASE_SIZE", "0.02"))

SOL = "So11111111111111111111111111111111111111112"

# ================= GLOBAL =================
rpc_index = 0
client = AsyncClient(RPCS[rpc_index])

last_log = {}
TOKEN_COOLDOWN = defaultdict(float)

# ================= UTIL =================
def log_once(key, msg, sec=5):
    now = asyncio.get_event_loop().time()
    if now - last_log.get(key, 0) > sec:
        print(msg)
        engine.logs.append(msg)
        last_log[key] = now

def get_client():
    global rpc_index, client
    try:
        return client
    except:
        rpc_index = (rpc_index + 1) % len(RPCS)
        client = AsyncClient(RPCS[rpc_index])
        return client

# ================= WALLET =================
def load_keypair():
    return Keypair.from_base58_string(PRIVATE_KEY)

# ================= JUPITER =================
async def jupiter_swap(input_mint, output_mint, amount):
    try:
        async with httpx.AsyncClient(timeout=10) as session:

            # 1️⃣ quote
            quote_url = "https://quote-api.jup.ag/v6/quote"
            params = {
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": int(amount * 1e9),
                "slippageBps": SLIPPAGE_BPS,
            }

            q = await session.get(quote_url, params=params)
            data = q.json()

            if "data" not in data or not data["data"]:
                log_once("no_route", f"NO ROUTE {output_mint}")
                return None

            route = data["data"][0]

            # 2️⃣ swap
            swap_url = "https://quote-api.jup.ag/v6/swap"

            body = {
                "route": route,
                "userPublicKey": str(load_keypair().pubkey()),
                "wrapAndUnwrapSol": True,
                "dynamicComputeUnitLimit": True,
            }

            res = await session.post(swap_url, json=body)
            swap_data = res.json()

            if "swapTransaction" not in swap_data:
                log_once("no_tx", f"NO TX {output_mint}")
                return None

            return swap_data["swapTransaction"]

    except Exception as e:
        log_once("jup_err", f"JUP ERROR {str(e)}")
        return None

# ================= SEND TX =================
async def send_tx(tx_base64):
    try:
        keypair = load_keypair()

        tx = VersionedTransaction.from_bytes(base64.b64decode(tx_base64))
        tx.sign([keypair])

        client = get_client()

        sig = await client.send_raw_transaction(bytes(tx))
        log_once("tx_sent", f"SENT {sig.value}")

        # confirm
        for _ in range(10):
            res = await client.get_signature_statuses([sig.value])
            if res.value[0] and res.value[0].confirmation_status:
                log_once("tx_ok", f"CONFIRMED {sig.value}")
                return sig.value
            await asyncio.sleep(1)

        log_once("tx_timeout", f"TIMEOUT {sig.value}")
        return None

    except Exception as e:
        log_once("tx_fail", f"TX FAIL {str(e)}")
        return None

# ================= BUY =================
async def buy(token, combo):
    if TOKEN_COOLDOWN[token] > asyncio.get_event_loop().time():
        return

    log_once("try_buy", f"TRY BUY {token} combo={combo:.4f}")

    tx = await jupiter_swap(SOL, token, BASE_SIZE)

    if not tx:
        log_once("buy_fail", f"BUY FAIL {token}")
        return

    sig = await send_tx(tx)

    if sig:
        engine.positions.append({
            "token": token,
            "entry": combo,
            "size": BASE_SIZE,
            "tx": sig
        })
        engine.stats["buys"] += 1
        log_once("buy_ok", f"BUY OK {token}")

    TOKEN_COOLDOWN[token] = asyncio.get_event_loop().time() + 30

# ================= MAIN LOOP =================
async def bot_loop():
    while True:
        try:
            # 假資料（你會換成 scanner）
            tokens = [
                ("WIF", random.random()),
                ("BONK", random.random()),
                ("POPCAT", random.random()),
            ]

            ranked = sorted(tokens, key=lambda x: x[1], reverse=True)[:3]

            for token, combo in ranked:
                engine.stats["signals"] += 1

                if combo > 0.05:
                    await buy(token, combo)

            await asyncio.sleep(5)

        except Exception as e:
            engine.stats["errors"] += 1
            print("MAIN ERR", e)
            await asyncio.sleep(2)

# ================= FASTAPI =================
app = FastAPI()

@app.on_event("startup")
async def start():
    asyncio.create_task(bot_loop())

@app.get("/")
async def root():
    return JSONResponse({
        "positions": engine.positions,
        "stats": engine.stats,
        "logs": engine.logs[-50:]
    })
