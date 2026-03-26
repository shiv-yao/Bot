import os
import asyncio
import httpx
import random

from state import engine
from wallet import load_keypair
from jupiter import get_order, execute_order

RPC = os.getenv("RPC", "")
SOL = "So11111111111111111111111111111111111111112"

MODE = os.getenv("MODE", "REAL")

MAX_POSITIONS = 3
POSITION_SIZE = 0.002

TAKE_PROFIT = 0.3
STOP_LOSS = 0.12
TRAILING = 0.15

SMART_WALLETS = [
    # 👉 之後可換成你追蹤的聰明錢
]


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
        return (int(out)/1e9)/1_000_000
    except:
        return None


# ================= SMART MONEY =================

async def get_wallet_tokens(wallet):
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(RPC, json={
                "jsonrpc":"2.0",
                "id":1,
                "method":"getTokenAccountsByOwner",
                "params":[
                    wallet,
                    {"programId":"TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
                    {"encoding":"jsonParsed"}
                ]
            })
        data = r.json()
        tokens = []

        for item in data["result"]["value"]:
            info = item["account"]["data"]["parsed"]["info"]
            mint = info["mint"]
            amt = float(info["tokenAmount"]["uiAmount"] or 0)

            if amt > 0:
                tokens.append(mint)

        return tokens
    except:
        return []


async def smart_money_signal():
    for w in SMART_WALLETS:
        tokens = await get_wallet_tokens(w)
        if tokens:
            return random.choice(tokens)
    return None


# ================= RUG FILTER =================

async def rug_filter(mint):
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

        liquidity = data.get("otherAmountThreshold", 0)
        impact = data.get("priceImpactPct", 1)

        if liquidity < 100000:
            return False

        if impact > 0.1:
            return False

        return True

    except:
        return False


# ================= SNIPER =================

async def scan_market():
    test = [
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    ]
    return random.choice(test)


async def pick_token():
    # 優先 smart money
    sm = await smart_money_signal()
    if sm:
        engine.log("SMART MONEY SIGNAL")
        return sm

    # fallback 市場掃描
    return await scan_market()


# ================= BUY =================

async def buy(mint):
    if len(engine.positions) >= MAX_POSITIONS:
        return

    if any(p["token"] == mint for p in engine.positions):
        return

    if not await rug_filter(mint):
        engine.log("RUG FILTER BLOCK")
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
            "jsonrpc":"2.0",
            "id":1,
            "method":"sendTransaction",
            "params":[signed, {"skipPreflight":True}]
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

        await asyncio.sleep(5)


# ================= MAIN =================

async def bot_loop():
    engine.log("PHASE D START")

    asyncio.create_task(monitor())

    while True:
        mint = await pick_token()

        if mint:
            await buy(mint)

        engine.stats["signals"] += 1
        engine.last_signal = "phase_d"

        await asyncio.sleep(4)
