import os
import asyncio
import httpx

from state import engine
from wallet import load_keypair
from jupiter import get_order, execute_order
from mempool import mempool_stream
from wallet_graph import wallet_graph_signal
from alpha_engine import rank_candidates

RPC = os.getenv("RPC", "").strip()
SOL = "So11111111111111111111111111111111111111112"

MODE = os.getenv("MODE", "REAL").upper()

MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "3"))
POSITION_SIZE = float(os.getenv("POSITION_SIZE_SOL", "0.002"))
MIN_POSITION_SOL = float(os.getenv("MIN_POSITION_SOL", "0.001"))
MAX_POSITION_SOL = float(os.getenv("MAX_POSITION_SOL", "0.003"))
RISK_PCT_PER_TRADE = float(os.getenv("RISK_PCT_PER_TRADE", "0.10"))

TAKE_PROFIT = float(os.getenv("TAKE_PROFIT_PCT", "0.20"))
STOP_LOSS = float(os.getenv("STOP_LOSS_PCT", "0.08"))
TRAILING = float(os.getenv("TRAILING_STOP_PCT", "0.10"))

ENABLE_AUTO_SELL = os.getenv("ENABLE_AUTO_SELL", "true").lower() == "true"

CANDIDATES = set()


async def rpc_post(method: str, params: list):
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                RPC,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": method,
                    "params": params,
                },
            )
        return r.json()
    except Exception as e:
        engine.stats["errors"] += 1
        engine.log(f"RPC ERROR {e}")
        return None


async def sync_sol_balance():
    kp = load_keypair()
    if not kp:
        engine.log("SAFE MODE: no PRIVATE_KEY")
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
                "alpha_score": old.get("alpha_score", 0.0),
            })

    engine.positions = new_positions


async def get_price(mint: str):
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(
                "https://lite-api.jup.ag/swap/v1/quote",
                params={
                    "inputMint": mint,
                    "outputMint": SOL,
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
        engine.log(f"PRICE ERROR {e}")
        return None


async def send_signed_tx(signed_tx: str):
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
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

        if r.status_code != 200:
            return None, f"SEND TX FAILED: {r.text}"

        data = r.json()
        if "error" in data:
            return None, f"RPC ERROR: {data['error']}"

        return data, None
    except Exception as e:
        return None, f"SEND TX EXCEPTION: {e}"


async def rug_filter(mint: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://lite-api.jup.ag/swap/v1/quote",
                params={
                    "inputMint": SOL,
                    "outputMint": mint,
                    "amount": "100000000",
                    "slippageBps": 100,
                },
            )

        data = r.json()
        impact = data.get("priceImpactPct", 1)
        out_amount = data.get("outAmount", 0)

        if out_amount == 0:
            return False
        if impact > 0.15:
            return False

        return True
    except Exception:
        return False


def has_position(mint: str) -> bool:
    return any(p["token"] == mint for p in engine.positions)


def calc_position_size() -> float:
    capital = max(engine.capital, 0.0)
    raw = capital * RISK_PCT_PER_TRADE
    return max(MIN_POSITION_SOL, min(MAX_POSITION_SOL, raw))


async def buy(mint: str, alpha_score_value: float = 0.0):
    if MODE != "REAL":
        engine.log("PAPER MODE: buy skipped")
        return

    if len(engine.positions) >= MAX_POSITIONS:
        engine.log("BUY BLOCKED: MAX_POSITIONS")
        return

    if has_position(mint):
        engine.log(f"BUY BLOCKED: ALREADY HAVE {mint[:8]}")
        return

    if not await rug_filter(mint):
        engine.log(f"BUY BLOCKED: RUG FILTER {mint[:8]}")
        return

    kp = load_keypair()
    if not kp:
        engine.log("NO KEYPAIR")
        return

    size = calc_position_size()
    amount_atomic = int(size * 1e9)

    engine.log(f"TRY BUY {mint[:8]} size={size:.6f}")

    order = await get_order(
        input_mint=SOL,
        output_mint=mint,
        amount_atomic=amount_atomic,
        taker=str(kp.pubkey()),
    )
    if not order:
        engine.stats["errors"] += 1
        engine.log("BUY ORDER FAIL")
        return

    result = await execute_order(order, kp)
    if not result:
        engine.stats["errors"] += 1
        engine.log("BUY EXEC FAIL")
        return

    signed_tx = result.get("signed_tx")
    if not signed_tx:
        engine.stats["errors"] += 1
        engine.log("BUY NO SIGNED TX")
        return

    sig_json, err = await send_signed_tx(signed_tx)
    if err:
        engine.stats["errors"] += 1
        engine.log(err)
        return

    token_amount = 0.0
    try:
        if "outAmount" in order:
            token_amount = int(order["outAmount"]) / 1_000_000
    except Exception:
        pass

    entry = 0.0
    try:
        if token_amount > 0:
            entry = size / token_amount
    except Exception:
        pass

    if entry <= 0:
        price_now = await get_price(mint)
        if price_now and price_now > 0:
            entry = price_now

    if entry <= 0:
        entry = 1e-9

    engine.positions.append({
        "token": mint,
        "amount": token_amount,
        "entry_price": entry,
        "last_price": entry,
        "peak_price": entry,
        "pnl_pct": 0.0,
        "alpha_score": alpha_score_value,
    })

    engine.stats["buys"] += 1
    engine.last_trade = f"BUY {mint[:8]}"
    engine.trade_history.append({
        "side": "BUY",
        "mint": mint,
        "result": sig_json,
    })
    engine.trade_history = engine.trade_history[-50:]
    engine.log(f"BUY SUCCESS {mint[:8]}")


async def sell(position: dict):
    if MODE != "REAL":
        engine.log("PAPER MODE: sell skipped")
        return

    kp = load_keypair()
    if not kp:
        engine.log("SELL FAILED: no key")
        return

    mint = position["token"]
    amount_atomic = int(position["amount"] * 1_000_000)

    engine.log(f"TRY SELL {mint[:8]}")

    order = await get_order(
        input_mint=mint,
        output_mint=SOL,
        amount_atomic=amount_atomic,
        taker=str(kp.pubkey()),
    )
    if not order:
        engine.stats["errors"] += 1
        engine.log("SELL ORDER FAIL")
        return

    result = await execute_order(order, kp)
    if not result:
        engine.stats["errors"] += 1
        engine.log("SELL EXEC FAIL")
        return

    signed_tx = result.get("signed_tx")
    if not signed_tx:
        engine.stats["errors"] += 1
        engine.log("SELL NO SIGNED TX")
        return

    sig_json, err = await send_signed_tx(signed_tx)
    if err:
        engine.stats["errors"] += 1
        engine.log(err)
        return

    engine.positions = [p for p in engine.positions if p["token"] != mint]
    engine.stats["sells"] += 1
    engine.last_trade = f"SELL {mint[:8]}"
    engine.trade_history.append({
        "side": "SELL",
        "mint": mint,
        "result": sig_json,
    })
    engine.trade_history = engine.trade_history[-50:]
    engine.log(f"SELL SUCCESS {mint[:8]}")


async def monitor():
    while True:
        try:
            if ENABLE_AUTO_SELL:
                for p in list(engine.positions):
                    price = await get_price(p["token"])
                    if not price:
                        continue

                    entry = p.get("entry_price", 0.0)
                    if not entry or entry <= 0:
                        price_now = await get_price(p["token"])
                        if price_now and price_now > 0:
                            p["entry_price"] = price_now
                            p["last_price"] = price_now
                            p["peak_price"] = max(p.get("peak_price", 0.0), price_now)
                            entry = price_now
                            engine.log(f"FIX ENTRY PRICE {p['token'][:8]} {entry}")
                        else:
                            engine.log("SKIP MONITOR: invalid entry_price")
                            continue

                    p["last_price"] = price
                    p["peak_price"] = max(p.get("peak_price", price), price)

                    pnl = (price - entry) / entry
                    p["pnl_pct"] = pnl

                    engine.log(f"{p['token'][:8]} PNL {round(pnl * 100, 2)}%")

                    if pnl >= TAKE_PROFIT:
                        engine.log("TAKE PROFIT HIT")
                        await sell(p)
                        continue

                    if pnl <= -STOP_LOSS:
                        engine.log("STOP LOSS HIT")
                        await sell(p)
                        continue

                    peak = p.get("peak_price", price)
                    if peak > 0:
                        drawdown = (peak - price) / peak
                        if peak > entry and drawdown >= TRAILING:
                            engine.log("TRAILING STOP HIT")
                            await sell(p)
                            continue

        except Exception as e:
            engine.stats["errors"] += 1
            engine.log(f"MONITOR ERR {e}")

        await asyncio.sleep(5)


async def handle_mempool(event: dict):
    try:
        mint = event.get("mint")
        if not mint:
            return

        if len(mint) < 32 or len(mint) > 44:
            return

        CANDIDATES.add(mint)
        if len(CANDIDATES) > 100:
            CANDIDATES.pop()

        engine.log(f"CANDIDATE ADD {mint[:8]}")

    except Exception as e:
        engine.stats["errors"] += 1
        engine.log(f"MEMPOOL ERR {e}")


async def bot_loop():
    engine.mode = MODE
    engine.log("FUND MODE START")

    asyncio.create_task(monitor())
    asyncio.create_task(mempool_stream(handle_mempool))

    while True:
        try:
            await sync_sol_balance()
            await sync_positions()

            ranked = await rank_candidates(CANDIDATES)
            if ranked:
                best = ranked[0]
                mint = best["mint"]
                score = best["score"]

                engine.last_signal = f"alpha:{score:.2f}"
                engine.log(f"BEST {mint[:8]} score={score:.2f}")

                if score > 25:
                    await buy(mint, alpha_score_value=score)

            smart_money_mint = await wallet_graph_signal(RPC)

if smart_money_mint:
    engine.log(f"SMART MONEY {smart_money_mint[:8]}")
    await buy(smart_money_mint, alpha_score_value=999)
            if smart_money_mint and not has_position(smart_money_mint):
                engine.log(f"SMART MONEY HIT {smart_money_mint[:8]}")
                await buy(smart_money_mint, alpha_score_value=99.0)

            engine.stats["signals"] += 1
            engine.log("LOOP RUNNING")

        except Exception as e:
            engine.stats["errors"] += 1
            engine.log(f"LOOP ERROR {e}")

        await asyncio.sleep(4)
