import os
import asyncio
import httpx
import random

from state import engine
from wallet import load_keypair
from jupiter import get_order, execute_order

RPC = os.getenv("RPC", "").strip()
SOL_MINT = "So11111111111111111111111111111111111111112"

MODE = os.getenv("MODE", "PAPER").upper()

MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "3"))
POSITION_SIZE_SOL = float(os.getenv("POSITION_SIZE_SOL", "0.002"))

TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "0.2"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "0.08"))
TRAILING_STOP_PCT = float(os.getenv("TRAILING_STOP_PCT", "0.1"))

ENABLE_AUTO_SELL = True


# ================= RPC =================

async def rpc_post(method, params):
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(RPC, json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": method,
                "params": params
            })
            return r.json()
    except Exception as e:
        engine.log(f"RPC ERROR {e}")
        return None


# ================= PRICE =================

async def get_price(mint):
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(
                "https://lite-api.jup.ag/swap/v1/quote",
                params={
                    "inputMint": mint,
                    "outputMint": SOL_MINT,
                    "amount": "1000000",
                    "slippageBps": 100
                }
            )

        data = r.json()
        out = data.get("outAmount")
        if not out:
            return None

        sol = int(out) / 1e9
        return sol / 1_000_000

    except:
        return None


# ================= SNIPER（簡版） =================

async def scan_new_tokens():
    # 模擬（你之後可以接 pump.fun / mempool）
    test_list = [
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    ]
    return random.choice(test_list)


# ================= BUY =================

async def do_buy(mint):
    if len(engine.positions) >= MAX_POSITIONS:
        return

    if any(p["token"] == mint for p in engine.positions):
        return

    kp = load_keypair()
    if not kp:
        return

    amount_atomic = int(POSITION_SIZE_SOL * 1e9)

    engine.log(f"TRY BUY {mint[:6]}")

    order = await get_order(
        SOL_MINT,
        mint,
        amount_atomic,
        str(kp.pubkey())
    )
    if not order:
        engine.log("ORDER FAIL")
        return

    result = await execute_order(order, kp)
    if not result:
        engine.log("EXEC FAIL")
        return

    signed_tx = result.get("signed_tx")

    async with httpx.AsyncClient() as client:
        await client.post(RPC, json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendTransaction",
            "params": [signed_tx, {"skipPreflight": True}]
        })

    # ===== entry price 修正 =====
    entry = 0.0
    try:
        token_amount = int(order["outAmount"]) / 1_000_000
        if token_amount > 0:
            entry = POSITION_SIZE_SOL / token_amount
    except:
        pass

    if entry <= 0:
        price = await get_price(mint)
        if price:
            entry = price

    if entry <= 0:
        entry = 1e-9

    engine.positions.append({
        "token": mint,
        "amount": token_amount,
        "entry_price": entry,
        "last_price": entry,
        "peak_price": entry,
        "pnl_pct": 0
    })

    engine.stats["buys"] += 1
    engine.log("BUY SUCCESS")


# ================= SELL =================

async def do_sell(p):
    kp = load_keypair()
    if not kp:
        return

    mint = p["token"]
    amount_atomic = int(p["amount"] * 1_000_000)

    engine.log(f"SELL {mint[:6]}")

    order = await get_order(
        mint,
        SOL_MINT,
        amount_atomic,
        str(kp.pubkey())
    )
    if not order:
        return

    result = await execute_order(order, kp)
    if not result:
        return

    signed_tx = result.get("signed_tx")

    async with httpx.AsyncClient() as client:
        await client.post(RPC, json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendTransaction",
            "params": [signed_tx, {"skipPreflight": True}]
        })

    engine.positions = [x for x in engine.positions if x != p]
    engine.stats["sells"] += 1
    engine.log("SELL SUCCESS")


# ================= MONITOR =================

async def monitor():
    while True:
        try:
            for p in engine.positions:
                price = await get_price(p["token"])
                if not price:
                    continue

                entry = p["entry_price"]
                if entry <= 0:
                    continue

                p["last_price"] = price
                p["peak_price"] = max(p["peak_price"], price)

                pnl = (price - entry) / entry
                p["pnl_pct"] = pnl

                engine.log(f"{p['token'][:6]} PNL {round(pnl*100,2)}%")

                if pnl >= TAKE_PROFIT_PCT:
                    await do_sell(p)
                    continue

                if pnl <= -STOP_LOSS_PCT:
                    await do_sell(p)
                    continue

                drawdown = (p["peak_price"] - price) / p["peak_price"]
                if drawdown >= TRAILING_STOP_PCT:
                    await do_sell(p)
                    continue

        except Exception as e:
            engine.log(f"MONITOR ERR {e}")

        await asyncio.sleep(6)


# ================= MAIN LOOP =================

async def bot_loop():
    engine.mode = MODE
    engine.log("FUND MODE START")

    asyncio.create_task(monitor())

    while True:
        try:
            mint = await scan_new_tokens()

            if mint:
                await do_buy(mint)

            engine.stats["signals"] += 1
            engine.last_signal = "alpha_scan"

        except Exception as e:
            engine.log(f"LOOP ERR {e}")

        await asyncio.sleep(5)
