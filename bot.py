import os
import asyncio
import httpx

from state import engine
from wallet import load_keypair
from jupiter import get_order, execute_order

RPC = os.getenv("RPC", "").strip()
SOL_MINT = "So11111111111111111111111111111111111111112"

TEST_TARGET_MINT = os.getenv("TEST_TARGET_MINT", "").strip()
AUTO_TEST_BUY = os.getenv("AUTO_TEST_BUY", "false").lower() == "true"
BUY_AMOUNT_SOL = float(os.getenv("BUY_AMOUNT_SOL", "0.002"))
MODE = os.getenv("MODE", "PAPER").upper()

TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "0.15"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "0.08"))
TRAILING_STOP_PCT = float(os.getenv("TRAILING_STOP_PCT", "0.07"))
ENABLE_AUTO_SELL = os.getenv("ENABLE_AUTO_SELL", "false").lower() == "true"


async def rpc_post(method, params):
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                RPC,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": method,
                    "params": params,
                },
            )
            return resp.json()
    except Exception as e:
        engine.log(f"RPC ERROR: {e}")
        return None


async def sync_sol_balance():
    kp = load_keypair()
    if not kp:
        return

    res = await rpc_post("getBalance", [str(kp.pubkey())])
    if not res or "result" not in res:
        return

    lamports = res["result"]["value"]
    engine.sol_balance = lamports / 1e9
    engine.capital = engine.sol_balance


async def sync_positions():
    kp = load_keypair()
    if not kp:
        return

    res = await rpc_post(
        "getTokenAccountsByOwner",
        [
            str(kp.pubkey()),
            {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
            {"encoding": "jsonParsed"},
        ],
    )
    if not res or "result" not in res:
        return

    old_map = {p["token"]: p for p in engine.positions}
    new_positions = []

    for item in res["result"]["value"]:
        info = item["account"]["data"]["parsed"]["info"]
        mint = info["mint"]
        amount = float(info["tokenAmount"].get("uiAmount") or 0)

        if amount > 0:
            old = old_map.get(mint, {})
            entry = old.get("entry_price", 0.0)

            new_positions.append({
                "token": mint,
                "amount": amount,
                "entry_price": entry,
                "last_price": old.get("last_price", entry),
                "peak_price": old.get("peak_price", entry),
                "pnl_pct": old.get("pnl_pct", 0.0),
            })

    engine.positions = new_positions


async def get_price(mint):
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(
                "https://lite-api.jup.ag/swap/v1/quote",
                params={
                    "inputMint": mint,
                    "outputMint": SOL_MINT,
                    "amount": "1000000",
                    "slippageBps": 100,
                },
            )

        if r.status_code != 200:
            return None

        data = r.json()
        out_amount = data.get("outAmount")
        if not out_amount:
            return None

        out_sol = int(out_amount) / 1e9
        return out_sol / 1_000_000
    except Exception as e:
        engine.log(f"PRICE ERROR: {e}")
        return None


async def send_signed_tx(signed_tx_b64: str):
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            RPC,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendTransaction",
                "params": [
                    signed_tx_b64,
                    {
                        "skipPreflight": True,
                        "encoding": "base64",
                    },
                ],
            },
        )

    if resp.status_code != 200:
        return None, f"SEND TX FAILED: {resp.text}"

    data = resp.json()
    if "error" in data:
        return None, f"RPC ERROR: {data['error']}"

    return data, None


async def do_test_buy():
    if MODE != "REAL":
        engine.log("PAPER mode: skip real buy")
        return

    kp = load_keypair()
    if not kp:
        engine.log("NO KEYPAIR")
        return

    if not TEST_TARGET_MINT:
        engine.log("NO TEST_TARGET_MINT")
        return

    amount_atomic = int(BUY_AMOUNT_SOL * 1e9)

    engine.log("TRY ORDER")
    order = await get_order(
        input_mint=SOL_MINT,
        output_mint=TEST_TARGET_MINT,
        amount_atomic=amount_atomic,
        taker=str(kp.pubkey()),
    )
    if not order:
        engine.stats["errors"] += 1
        engine.log("ORDER FAILED")
        return

    engine.log("TRY EXECUTE")
    result = await execute_order(order, kp)
    if not result:
        engine.stats["errors"] += 1
        engine.log("EXECUTE FAILED")
        return

    signed_tx = result.get("signed_tx")
    if not signed_tx:
        engine.stats["errors"] += 1
        engine.log("NO SIGNED TX")
        return

    engine.log("TRY SEND TX")
    sig_json, err = await send_signed_tx(signed_tx)
    if err:
        engine.stats["errors"] += 1
        engine.log(err)
        return

    engine.stats["buys"] += 1
    engine.last_trade = f"BUY {TEST_TARGET_MINT[:8]}"

    token_amount = 0.0
    try:
        if "outAmount" in order:
            token_amount = int(order["outAmount"]) / 1_000_000
    except Exception:
        pass

    entry_price = 0.0
    if token_amount > 0:
        entry_price = BUY_AMOUNT_SOL / token_amount

    if entry_price <= 0:
        price_now = await get_price(TEST_TARGET_MINT)
        if price_now and price_now > 0:
            entry_price = price_now

    engine.positions = [{
        "token": TEST_TARGET_MINT,
        "amount": token_amount,
        "entry_price": entry_price,
        "last_price": entry_price,
        "peak_price": entry_price,
        "pnl_pct": 0.0,
    }]

    engine.trade_history.append({
        "side": "BUY",
        "mint": TEST_TARGET_MINT,
        "result": sig_json,
    })
    engine.trade_history = engine.trade_history[-50:]

    engine.log("BUY SUCCESS")


async def do_sell(mint, amount_atomic):
    kp = load_keypair()
    if not kp:
        engine.log("SELL FAILED: no key")
        return False

    engine.log("TRY SELL ORDER")
    order = await get_order(
        input_mint=mint,
        output_mint=SOL_MINT,
        amount_atomic=amount_atomic,
        taker=str(kp.pubkey()),
    )
    if not order:
        engine.stats["errors"] += 1
        engine.log("SELL ORDER FAILED")
        return False

    engine.log("TRY SELL EXECUTE")
    result = await execute_order(order, kp)
    if not result:
        engine.stats["errors"] += 1
        engine.log("SELL EXECUTE FAILED")
        return False

    signed_tx = result.get("signed_tx")
    if not signed_tx:
        engine.stats["errors"] += 1
        engine.log("SELL NO SIGNED TX")
        return False

    sig_json, err = await send_signed_tx(signed_tx)
    if err:
        engine.stats["errors"] += 1
        engine.log(err)
        return False

    engine.stats["sells"] += 1
    engine.last_trade = f"SELL {mint[:8]}"
    engine.trade_history.append({
        "side": "SELL",
        "mint": mint,
        "result": sig_json,
    })
    engine.trade_history = engine.trade_history[-50:]
    engine.log("SELL SUCCESS")
    engine.positions = []
    return True


async def monitor():
    while True:
        try:
            if ENABLE_AUTO_SELL and engine.positions:
                p = engine.positions[0]
                price = await get_price(p["token"])
                if not price:
                    await asyncio.sleep(8)
                    continue

                entry = p.get("entry_price", 0.0)
                if not entry or entry <= 0:
                    engine.log("SKIP MONITOR: invalid entry_price")
                    await asyncio.sleep(8)
                    continue

                p["last_price"] = price
                p["peak_price"] = max(p.get("peak_price", price), price)

                pnl = (price - entry) / entry
                p["pnl_pct"] = pnl

                engine.log(f"PNL {round(pnl * 100, 2)}%")

                if pnl >= TAKE_PROFIT_PCT:
                    engine.log("TAKE PROFIT HIT")
                    await do_sell(p["token"], int(p["amount"] * 1_000_000))
                    await asyncio.sleep(8)
                    continue

                if pnl <= -STOP_LOSS_PCT:
                    engine.log("STOP LOSS HIT")
                    await do_sell(p["token"], int(p["amount"] * 1_000_000))
                    await asyncio.sleep(8)
                    continue

                peak = p.get("peak_price", price)
                if peak > 0:
                    drawdown = (peak - price) / peak
                    if peak > entry and drawdown >= TRAILING_STOP_PCT:
                        engine.log("TRAILING STOP HIT")
                        await do_sell(p["token"], int(p["amount"] * 1_000_000))
                        await asyncio.sleep(8)
                        continue

        except Exception as e:
            engine.log(f"MONITOR ERROR {e}")

        await asyncio.sleep(8)


async def bot_loop():
    engine.mode = MODE
    engine.log("BOT LOOP STARTED")

    asyncio.create_task(monitor())

    bought = False

    while True:
        try:
            await sync_sol_balance()
            await sync_positions()

            if AUTO_TEST_BUY and not bought:
                await do_test_buy()
                bought = True

            engine.stats["signals"] += 1
            engine.last_signal = "heartbeat"
            engine.log("LOOP RUNNING")

        except Exception as e:
            engine.log(f"ERROR {e}")

        await asyncio.sleep(8)
