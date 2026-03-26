import os
import asyncio
import httpx
import random
import base64

from mempool import mempool_stream
from wallet_graph import wallet_graph_signal

from state import engine
from wallet import load_keypair
from jupiter import get_order, execute_order

RPC = os.getenv("RPC", "")
SOL = "So11111111111111111111111111111111111111112"

MAX_POSITIONS = 5
BASE_SIZE = 0.002

TAKE_PROFIT = 0.4
STOP_LOSS = 0.15
TRAILING = 0.2

# ================= JITO =================

JITO_URL = "https://mainnet.block-engine.jito.wtf/api/v1/bundles"

async def send_jito(tx):
    try:
        async with httpx.AsyncClient() as client:
            await client.post(JITO_URL, json={
                "transactions": [tx]
            })
        engine.log("JITO SENT")
    except Exception as e:
        engine.log(f"JITO ERROR {e}")


# ================= PRICE =================

async def get_price(mint):
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                "https://lite-api.jup.ag/swap/v1/quote",
                params={
                    "inputMint": mint,
                    "outputMint": SOL,
                    "amount": "1000000"
                }
            )
        data = r.json()
        out = data.get("outAmount")
        if not out:
            return None
        return (int(out)/1e9)/1_000_000
    except:
        return None


# ================= MEMPOOL (簡化版) =================

async def mempool_sniper():
    # ⚠️ 這裡是簡化版（真實版要 websocket decode）
    hot = [
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    ]
    return random.choice(hot)


# ================= RUG FILTER =================

async def rug_filter(mint):
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                "https://lite-api.jup.ag/swap/v1/quote",
                params={
                    "inputMint": SOL,
                    "outputMint": mint,
                    "amount": "100000000"
                }
            )
        data = r.json()

        impact = data.get("priceImpactPct", 1)
        if impact > 0.15:
            return False

        return True
    except:
        return False


# ================= SIZE ENGINE =================

def get_size():
    # 簡單資金管理（之後可升級 Kelly）
    return BASE_SIZE


# ================= BUY =================

async def buy(mint):
    if len(engine.positions) >= MAX_POSITIONS:
        return

    if any(p["token"] == mint for p in engine.positions):
        return

    if not await rug_filter(mint):
        engine.log("RUG BLOCK")
        return

    kp = load_keypair()
    if not kp:
        return

    size = get_size()
    amt = int(size * 1e9)

    order = await get_order(SOL, mint, amt, str(kp.pubkey()))
    if not order:
        return

    result = await execute_order(order, kp)
    if not result:
        return

    signed = result.get("signed_tx")

    # 🚀 JITO（優先）
    await send_jito(signed)

    # fallback RPC
    async with httpx.AsyncClient() as client:
        await client.post(RPC, json={
            "jsonrpc":"2.0",
            "id":1,
            "method":"sendTransaction",
            "params":[signed, {"skipPreflight":True}]
        })

    token_amount = int(order["outAmount"]) / 1_000_000
    entry = size / token_amount if token_amount > 0 else 1e-9

    engine.positions.append({
        "token": mint,
        "amount": token_amount,
        "entry_price": entry,
        "last_price": entry,
        "peak_price": entry,
        "pnl_pct": 0
    })

    engine.log(f"BUY {mint[:6]}")


# ================= SELL =================

async def sell(p):
    kp = load_keypair()
    if not kp:
        return

    mint = p["token"]
    amt = int(p["amount"] * 1_000_000)

    order = await get_order(mint, SOL, amt, str(kp.pubkey()))
    if not order:
        return

    result = await execute_order(order, kp)
    if not result:
        return

    signed = result.get("signed_tx")

    await send_jito(signed)

    async with httpx.AsyncClient() as client:
        await client.post(RPC, json={
            "jsonrpc":"2.0",
            "id":1,
            "method":"sendTransaction",
            "params":[signed, {"skipPreflight":True}]
        })

    engine.positions = [x for x in engine.positions if x != p]
    engine.log(f"SELL {mint[:6]}")


# ================= MONITOR =================

async def monitor():
    while True:
        for p in engine.positions:
            price = await get_price(p["token"])
            if not price:
                continue

            entry = p["entry_price"]

            p["last_price"] = price
            p["peak_price"] = max(p["peak_price"], price)

            pnl = (price - entry) / entry
            p["pnl_pct"] = pnl

            engine.log(f"{p['token'][:6]} {round(pnl*100,2)}%")

            if pnl >= TAKE_PROFIT:
                await sell(p)
                continue

            if pnl <= -STOP_LOSS:
                await sell(p)
                continue

            dd = (p["peak_price"] - price) / p["peak_price"]
            if dd >= TRAILING:
                await sell(p)

        await asyncio.sleep(3)


# ================= MAIN =================

async def bot_loop():
    engine.log("GOD MODE START")

    asyncio.create_task(monitor())

    while True:
        mint = await mempool_sniper()

        if mint:
            await buy(mint)

        engine.stats["signals"] += 1
        engine.last_signal = "god_mode"

        await asyncio.sleep(2)
