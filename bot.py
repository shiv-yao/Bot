import asyncio
import os
from typing import Any

import httpx

from state import engine
from wallet import load_keypair
from jupiter import get_order, execute_order

RPC = os.getenv("RPC", "").strip()
SOL_MINT = "So11111111111111111111111111111111111111112"

# 測試標的，先用你自己想買的 mint 換掉
TEST_TARGET_MINT = os.getenv("TEST_TARGET_MINT", "").strip()

# 預設先不要自動買，避免誤下單
AUTO_TEST_BUY = os.getenv("AUTO_TEST_BUY", "false").lower() == "true"

# 0.002 SOL
BUY_AMOUNT_SOL = float(os.getenv("BUY_AMOUNT_SOL", "0.002"))
MODE = os.getenv("MODE", "PAPER").upper()

def rpc_payload(method: str, params: list[Any]) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    }

async def rpc_post(method: str, params: list[Any]) -> dict | None:
    if not RPC:
        engine.log("❌ RPC not set")
        return None

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(RPC, json=rpc_payload(method, params))
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        engine.stats["errors"] += 1
        engine.log(f"RPC error: {e}")
        return None

async def sync_sol_balance() -> None:
    kp = load_keypair()
    if not kp:
        engine.log("SAFE MODE: no PRIVATE_KEY")
        return

    result = await rpc_post("getBalance", [str(kp.pubkey())])
    if not result or "result" not in result:
        return

    lamports = result["result"]["value"]
    engine.sol_balance = lamports / 1e9
    engine.capital = engine.sol_balance

async def sync_positions() -> None:
    kp = load_keypair()
    if not kp:
        return

    result = await rpc_post(
        "getTokenAccountsByOwner",
        [
            str(kp.pubkey()),
            {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
            {"encoding": "jsonParsed"},
        ],
    )
    if not result or "result" not in result:
        return

    positions = []
    for item in result["result"]["value"]:
        info = item["account"]["data"]["parsed"]["info"]
        mint = info["mint"]
        amount = float(info["tokenAmount"].get("uiAmount") or 0)
        if amount > 0:
            positions.append({
                "token": mint,
                "amount": amount,
            })

    engine.positions = positions

import httpx

async def do_test_buy() -> None:
    if MODE != "REAL":
        engine.log("PAPER mode: skip real buy")
        return

    if not TEST_TARGET_MINT:
        engine.log("No TEST_TARGET_MINT set")
        return

    kp = load_keypair()
    if not kp:
        engine.log("No PRIVATE_KEY, cannot trade")
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

    async with httpx.AsyncClient(timeout=30) as client:
        sig = await client.post(
            RPC,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendTransaction",
                "params": [
                    signed_tx,
                    {
                        "skipPreflight": True,
                        "encoding": "base64",
                    },
                ],
            },
        )

    if sig.status_code != 200:
        engine.stats["errors"] += 1
        engine.log(f"SEND TX FAILED: {sig.text}")
        return

    sig_json = sig.json()
    if "error" in sig_json:
        engine.stats["errors"] += 1
        engine.log(f"RPC ERROR: {sig_json['error']}")
        return

    engine.stats["buys"] += 1
    engine.last_trade = f"BUY {TEST_TARGET_MINT[:8]}"
    engine.trade_history.append({
        "side": "BUY",
        "mint": TEST_TARGET_MINT,
        "result": sig_json,
    })
    engine.trade_history = engine.trade_history[-50:]
    engine.log("BUY SUCCESS")

async def bot_loop() -> None:
    engine.mode = MODE
    engine.log("BOT LOOP STARTED")

    bought_once = False

    while True:
        try:
            await sync_sol_balance()
            await sync_positions()

            # Phase A：只做一次測試買入
            if AUTO_TEST_BUY and not bought_once:
                await do_test_buy()
                bought_once = True

            engine.last_signal = "phase_a_heartbeat"
            engine.stats["signals"] += 1
            engine.log("LOOP RUNNING")

        except Exception as e:
            engine.stats["errors"] += 1
            engine.log(f"BOT LOOP ERROR: {e}")

        await asyncio.sleep(8)

import os
import httpx
import asyncio

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

async def get_token_price_in_sol(mint: str) -> float | None:
    # 用 1 token 的 10^6 單位近似，不夠精準但先可用
    amount_atomic = 1_000_000

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                "https://lite-api.jup.ag/swap/v1/quote",
                params={
                    "inputMint": mint,
                    "outputMint": SOL_MINT,
                    "amount": str(amount_atomic),
                    "slippageBps": 100,
                },
            )
        if resp.status_code != 200:
            return None

        data = resp.json()
        out_amount = data.get("outAmount")
        if not out_amount:
            return None

        # outAmount 是 lamports，這裡近似每 1e6 token atomic 的 SOL
        return (int(out_amount) / 1e9) / 1_000_000

    except Exception as e:
        engine.log(f"PRICE ERROR: {e}")
        return None
