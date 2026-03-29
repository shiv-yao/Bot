# ================= v1321 REAL SNIPER (ON-CHAIN) =================
import asyncio
import time
import base64
import os
from collections import defaultdict

import httpx
import base58

from fastapi import FastAPI
from state import engine

from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from solders import message as solders_message

HTTP = httpx.AsyncClient(timeout=15)

# ================= CONFIG =================
SOL = "So11111111111111111111111111111111111111112"

JUP_ORDER = "https://api.jup.ag/swap/v2/order"
JUP_EXECUTE = "https://api.jup.ag/swap/v2/execute"

RPC = "https://api.mainnet-beta.solana.com"

PRIVATE_KEY = os.getenv("PRIVATE_KEY_B58", "")
REAL = os.getenv("REAL_TRADING", "false").lower() == "true"

# ================= WALLET =================
KEYPAIR = None
if PRIVATE_KEY:
    KEYPAIR = Keypair.from_bytes(base58.b58decode(PRIVATE_KEY))

def pubkey():
    return str(KEYPAIR.pubkey())

# ================= GLOBAL =================
CANDIDATES = {"BONK","WIF","JUP","MYRO","POPCAT"}
TOKEN_COOLDOWN = defaultdict(float)

IN_FLIGHT = set()
LAST_LOG = {}

# ================= UTIL =================
def now():
    return time.time()

def log(msg):
    engine.logs.append(msg)
    engine.logs = engine.logs[-200:]
    print(msg)

def log_once(k, msg, t=5):
    if now() - LAST_LOG.get(k,0) > t:
        LAST_LOG[k] = now()
        log(msg)

# ================= PRICE =================
async def get_price(m):
    return 0.0001 + abs(hash(m)) % 1000 / 1e7

# ================= ALPHA =================
async def alpha(m):
    p1 = await get_price(m)
    await asyncio.sleep(0.2)
    p2 = await get_price(m)
    return (p2 - p1) / p1 if p1 else 0

# ================= SIGN =================
def sign_tx(tx_b64):
    raw = VersionedTransaction.from_bytes(base64.b64decode(tx_b64))
    msg = solders_message.to_bytes_versioned(raw.message)
    sig = KEYPAIR.sign_message(msg)

    sigs = list(raw.signatures)
    sigs[0] = sig

    signed = VersionedTransaction.populate(raw.message, sigs)
    return base64.b64encode(bytes(signed)).decode()

# ================= JUP =================
async def jupiter_order(input_mint, output_mint, amount):

    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": str(int(amount)),
        "slippageBps": 80,
    }

    if REAL:
        params["taker"] = pubkey()

    try:
        r = await HTTP.get(JUP_ORDER, params=params)
        data = r.json()

        if data.get("transaction"):
            return data

    except Exception as e:
        log_once("jup_err", str(e))

    # fallback
    return {"_quote_only": True}

async def jupiter_execute(order):

    signed = sign_tx(order["transaction"])

    payload = {
        "signedTransaction": signed,
        "requestId": order["requestId"],
    }

    r = await HTTP.post(JUP_EXECUTE, json=payload)
    data = r.json()

    if data.get("status") != "Success":
        raise Exception(data)

    return data["signature"]

# ================= RPC CONFIRM =================
async def confirm(sig):
    for _ in range(10):
        r = await HTTP.post(RPC, json={
            "jsonrpc":"2.0",
            "id":1,
            "method":"getSignatureStatuses",
            "params":[[sig]]
        })
        res = r.json()

        if res.get("result"):
            v = res["result"]["value"][0]
            if v and v.get("confirmationStatus") in ["confirmed","finalized"]:
                return True

        await asyncio.sleep(1)

    return False

# ================= BUY =================
async def buy(m, combo):

    if m in IN_FLIGHT:
        return

    IN_FLIGHT.add(m)

    try:
        if now() - TOKEN_COOLDOWN[m] < 10:
            return

        log_once("try", f"TRY {m}")

        order = await jupiter_order(SOL, m, 1000000)

        if not order:
            return

        if order.get("_quote_only"):
            log_once("quote", f"QUOTE_ONLY {m}")
            return

        if not REAL:
            log(f"PAPER BUY {m}")
            return

        sig = await jupiter_execute(order)

        await confirm(sig)

        engine.positions.append({
            "token": m,
            "entry_price": await get_price(m),
            "sig": sig,
            "ts": now()
        })

        TOKEN_COOLDOWN[m] = now()
        engine.stats["buys"] += 1

        log(f"BUY {m} sig={sig[:8]}")

    finally:
        IN_FLIGHT.discard(m)

# ================= LOOP =================
async def main_loop():
    while True:
        try:
            for m in CANDIDATES:
                c = await alpha(m)
                if c > 0.03:
                    await buy(m, c)

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"ERR {e}")

        await asyncio.sleep(3)

# ================= APP =================
app = FastAPI()

@app.on_event("startup")
async def start():
    engine.positions = []
    engine.logs = []
    engine.stats = {"buys":0,"sells":0,"errors":0}

    asyncio.create_task(main_loop())

@app.get("/")
def root():
    return {
        "positions": engine.positions,
        "stats": engine.stats,
        "logs": engine.logs[-20:]
    }
