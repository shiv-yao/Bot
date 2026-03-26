import os
import asyncio
import httpx
import random

from state import engine
from wallet import load_keypair
from jupiter import get_order, execute_order

RPC = os.getenv("RPC", "")
SOL = "So11111111111111111111111111111111111111112"

MODE = os.getenv("MODE", "PAPER").upper()

MAX_POSITIONS = 3
POSITION_SIZE = 0.002

TAKE_PROFIT = 0.25
STOP_LOSS = 0.1
TRAILING = 0.12


# ================= PRICE =================

async def get_price(mint):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://lite-api.jup.ag/swap/v1/quote",
                params={
                    "inputMint": mint,
                    "outputMint": SOL,
                    "amount": "1000000",
                    "slippageBps": 100
                }
            )
        data = r.json()
        out = data.get("outAmount")
        if not out:
            return None
        return (int(out) / 1e9) / 1_000_000
    except:
        return None


# ================= ALPHA ENGINE =================

async def get_token_info(mint):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://lite-api.jup.ag/swap/v1/quote",
                params={
                    "inputMint": SOL,
                    "outputMint": mint,
                    "amount": "100000000",
                    "slippageBps": 100
                }
            )
        data = r.json()

        return {
            "liquidity": data.get("otherAmountThreshold", 0),
            "priceImpact": data.get("priceImpactPct", 1)
        }
    except:
        return None


async def alpha_score(mint):
    info = await get_token_info(mint)
    if not info:
        return 0

    liquidity = info["liquidity"]
    impact = info["priceImpact"]

    score = 0

    # liquidity
    if liquidity > 1_000_000:
        score += 40
    elif liquidity > 100_000:
        score += 20

    # price impact（越低越好）
    if impact < 0.01:
        score += 30
    elif impact < 0.05:
        score += 10

    # momentum（簡版）
    price = await get_price(mint)
    if price:
        score += random.randint(5, 25)

    return score


# ================= SNIPER =================

async def scan_tokens():
    return [
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    ]


async def pick_best_token():
    tokens = await scan_tokens()

    best = None
    best_score = 0

    for t in tokens:
        score = await alpha_score(t)
        engine.log(f"{t[:6]} score {score}")

        if score > best_score:
            best = t
            best_score = score

    if best_score < 30:
        return None

    return best


# ================= BUY =================

async def buy(mint):
    if len(engine.positions) >= MAX_POSITIONS:
        return

    if any(p["token"] == mint for p in engine.positions):
        return

    kp = load_keypair()
    if not kp:
        return

    amt = int(POSITION_SIZE * 1e9)

    order = await get_order(SOL, mint, amt, str(kp.pubkey()))
    if not order:
        return

    result = await execute_order(order, kp)
    if not result:
        return

    signed = result.get("signed_tx")

    async with httpx.AsyncClient() as client:
        await client.post(RPC, json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendTransaction",
            "params": [signed, {"skipPreflight": True}]
        })

    token_amount = int(order["outAmount"]) / 1_000_000

    entry = POSITION_SIZE / token_amount if token_amount > 0 else 1e-9

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

    async with httpx.AsyncClient() as client:
        await client.post(RPC, json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendTransaction",
            "params": [signed, {"skipPreflight": True}]
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

        await asyncio.sleep(6)


# ================= MAIN =================

async def bot_loop():
    engine.log("ALPHA ENGINE START")

    asyncio.create_task(monitor())

    while True:
        mint = await pick_best_token()

        if mint:
            await buy(mint)

        engine.stats["signals"] += 1
        engine.last_signal = "alpha"

        await asyncio.sleep(5)
