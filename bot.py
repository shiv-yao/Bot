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

# === 風控 ===
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "0.15"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "0.08"))
TRAILING_STOP_PCT = float(os.getenv("TRAILING_STOP_PCT", "0.07"))
ENABLE_AUTO_SELL = os.getenv("ENABLE_AUTO_SELL", "false").lower() == "true"


# ================= RPC =================
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


# ================= 餘額 =================
async def sync_sol_balance():
    kp = load_keypair()
    if not kp:
        return

    res = await rpc_post("getBalance", [str(kp.pubkey())])
    if not res:
        return

    lamports = res["result"]["value"]
    engine.sol_balance = lamports / 1e9
    engine.capital = engine.sol_balance


# ================= 持倉同步（不覆蓋 entry）=================
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
    if not res:
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


# ================= 買 =================
async def do_test_buy():
    if MODE != "REAL":
        return

    kp = load_keypair()
    if not kp:
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
        engine.log("ORDER FAILED")
        return

    result = await execute_order(order, kp)
    if not result:
        engine.log("EXECUTE FAILED")
        return

    signed_tx = result.get("signed_tx")

    async with httpx.AsyncClient() as client:
        sig = await client.post(
            RPC,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendTransaction",
                "params": [signed_tx, {"skipPreflight": True}],
            },
        )

    sig_json = sig.json()

    engine.stats["buys"] += 1
    engine.last_trade = "BUY"

    token_amount = 0.0
    if "outAmount" in order:
        token_amount = int(order["outAmount"]) / 1_000_000

    entry_price = BUY_AMOUNT_SOL / token_amount if token_amount else 0

    # 🔥 關鍵：寫入 entry
    engine.positions = [{
        "token": TEST_TARGET_MINT,
        "amount": token_amount,
        "entry_price": entry_price,
        "last_price": entry_price,
        "peak_price": entry_price,
        "pnl_pct": 0.0
    }]

    engine.trade_history.append({
        "side": "BUY",
        "mint": TEST_TARGET_MINT,
        "result": sig_json
    })

    engine.log("BUY SUCCESS")


# ================= 價格 =================
async def get_price(mint):
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                "https://lite-api.jup.ag/swap/v1/quote",
                params={
                    "inputMint": mint,
                    "outputMint": SOL_MINT,
                    "amount": "1000000",
                },
            )
        data = r.json()
        out = int(data["outAmount"]) / 1e9
        return out / 1_000_000
    except:
        return None


# ================= 賣 =================
async def do_sell(mint, amount_atomic):
    kp = load_keypair()

    order = await get_order(
        input_mint=mint,
        output_mint=SOL_MINT,
        amount_atomic=amount_atomic,
        taker=str(kp.pubkey()),
    )

    result = await execute_order(order, kp)
    signed_tx = result["signed_tx"]

    async with httpx.AsyncClient() as client:
        sig = await client.post(
            RPC,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendTransaction",
                "params": [signed_tx, {"skipPreflight": True}],
            },
        )

    engine.stats["sells"] += 1
    engine.log("SELL SUCCESS")
    engine.positions = []


# ================= 監控 =================
async def monitor():
    while True:
        try:
            if ENABLE_AUTO_SELL and engine.positions:
                p = engine.positions[0]
                price = await get_price(p["token"])
                if not price:
                    continue

                entry = p["entry_price"]
                p["last_price"] = price
                p["peak_price"] = max(p["peak_price"], price)

                pnl = (price - entry) / entry
                p["pnl_pct"] = pnl

                engine.log(f"PNL {round(pnl*100,2)}%")

                # TP
                if pnl >= TAKE_PROFIT_PCT:
                    await do_sell(p["token"], int(p["amount"] * 1_000_000))

                # SL
                elif pnl <= -STOP_LOSS_PCT:
                    await do_sell(p["token"], int(p["amount"] * 1_000_000))

                # trailing
                drawdown = (p["peak_price"] - price) / p["peak_price"]
                if drawdown >= TRAILING_STOP_PCT:
                    await do_sell(p["token"], int(p["amount"] * 1_000_000))

        except Exception as e:
            engine.log(f"MONITOR ERROR {e}")

        await asyncio.sleep(8)


# ================= 主 loop =================
async def bot_loop():
    engine.log("BOT START")

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
