import asyncio
import aiohttp
import os
import base64
from datetime import datetime
from typing import Optional

import base58
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

from state import engine, log
from db import (
    log_event, insert_trade, upsert_position, close_position,
    fetch_open_positions, fetch_recent_trades
)
from turboquant_adapter import turboquant_score
from state import engine

engine.logs.append("bot started")
CONFIG = {
    "MODE": os.getenv("MODE", "PAPER"),
    "BUY_AMOUNT_SOL": float(os.getenv("BUY_AMOUNT_SOL", "0.002")),
    "MIN_FLOW": float(os.getenv("MIN_FLOW", "1200")),
    "MIN_WALLETS": int(os.getenv("MIN_WALLETS", "4")),
    "RUG_THRESHOLD": float(os.getenv("RUG_THRESHOLD", "0.7")),
    "TAKE_PROFIT": float(os.getenv("TAKE_PROFIT", "0.15")),
    "STOP_LOSS": float(os.getenv("STOP_LOSS", "-0.08")),
    "TRAILING_STOP": float(os.getenv("TRAILING_STOP", "0.07")),
    "MAX_HOLD_SEC": int(os.getenv("MAX_HOLD_SEC", "180")),
    "MAX_OPEN_POSITIONS": int(os.getenv("MAX_OPEN_POSITIONS", "3")),
    "SCALE_OUT_PCT": float(os.getenv("SCALE_OUT_PCT", "0.5"))
}

RPC = os.getenv("RPC")
BIRDEYE = os.getenv("BIRDEYE_API_KEY")
JUP_API_KEY = os.getenv("JUP_API_KEY")
SOL = "So11111111111111111111111111111111111111112"

if not RPC:
    raise Exception("RPC not set")

WS = RPC.replace("https", "wss")
wallet: Optional[Keypair] = None

def load_wallet():
    global wallet
    pk = os.getenv("PRIVATE_KEY")
    if not pk:
        log("⚠️ PRIVATE_KEY not set; SAFE mode only")
        return
    try:
        if "," in pk:
            wallet = Keypair.from_bytes(bytes(list(map(int, pk.split(",")))))
        else:
            wallet = Keypair.from_bytes(base58.b58decode(pk))
        log("✅ Wallet loaded")
    except Exception as e:
        raise Exception(f"PRIVATE_KEY invalid: {e}")

load_wallet()
flow_cache = {}
blacklist = set()

def jup_headers():
    headers = {}
    if JUP_API_KEY:
        headers["x-api-key"] = JUP_API_KEY
    return headers

def rug_score(flow: float, wallets: int, momentum: float) -> float:
    score = 0.0
    if wallets < 3:
        score += 0.4
    if momentum < flow * 0.2:
        score += 0.3
    if flow > 2000 and wallets < 5:
        score += 0.3
    return score

def local_ai_score(flow: float, wallets: int, momentum: float) -> float:
    score = 0.0
    if flow > 2000:
        score += 0.4
    if wallets >= 6:
        score += 0.3
    if momentum > flow * 0.35:
        score += 0.3
    return score

async def get_price(session: aiohttp.ClientSession, mint: str):
    if not BIRDEYE:
        return None
    try:
        async with session.get(
            f"https://public-api.birdeye.so/defi/price?address={mint}",
            headers={"X-API-KEY": BIRDEYE},
            timeout=10,
        ) as r:
            j = await r.json()
        return j.get("data", {}).get("value")
    except Exception:
        return None

async def get_sol_balance(session: aiohttp.ClientSession) -> float:
    if wallet is None:
        return 0.0
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getBalance",
        "params": [str(wallet.pubkey())]
    }
    try:
        async with session.post(RPC, json=payload, timeout=10) as r:
            data = await r.json()
        lamports = data.get("result", {}).get("value", 0)
        return lamports / 1e9
    except Exception:
        return 0.0

async def get_wallet_positions(session: aiohttp.ClientSession):
    if wallet is None:
        return []
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTokenAccountsByOwner",
        "params": [
            str(wallet.pubkey()),
            {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
            {"encoding": "jsonParsed"}
        ]
    }
    try:
        async with session.post(RPC, json=payload, timeout=15) as r:
            data = await r.json()

        positions = []
        for item in data.get("result", {}).get("value", []):
            info = item["account"]["data"]["parsed"]["info"]
            mint = info["mint"]
            amount = float(info["tokenAmount"].get("uiAmount") or 0)
            if amount > 0:
                positions.append({"token": mint, "amount": amount})
        return positions
    except Exception as e:
        log(f"position sync error: {e}")
        return []

async def sync_positions_loop(session: aiohttp.ClientSession):
    while True:
        try:
            engine["sol_balance"] = round(await get_sol_balance(session), 6)
            engine["capital"] = engine["sol_balance"]

            real_positions = await get_wallet_positions(session)
            meta = {p.get("token"): p for p in engine.get("positions", [])}
            synced = []
            for p in real_positions:
                row = {"token": p["token"], "amount": p["amount"]}
                if p["token"] in meta:
                    row.update({
                        "entry_price": meta[p["token"]].get("entry_price", 0),
                        "score": meta[p["token"]].get("score", 0),
                        "opened_at": meta[p["token"]].get("opened_at", ""),
                        "pnl": meta[p["token"]].get("pnl", 0),
                        "peak_price": meta[p["token"]].get("peak_price", meta[p["token"]].get("entry_price", 0)),
                        "scaled_out": meta[p["token"]].get("scaled_out", False),
                    })
                synced.append(row)
            engine["positions"] = synced
        except Exception as e:
            engine["stats"]["errors"] += 1
            log(f"sync loop error: {e}")
            log_event("ERROR", f"sync loop error: {e}")
        await asyncio.sleep(5)

async def jupiter_order(session: aiohttp.ClientSession, input_mint: str, output_mint: str, amount_atoms: int):
    if wallet is None:
        return None
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": str(amount_atoms),
        "taker": str(wallet.pubkey()),
    }
    async with session.get(
        "https://api.jup.ag/swap/v2/order",
        params=params,
        headers=jup_headers(),
        timeout=20,
    ) as r:
        if r.status >= 400:
            body = await r.text()
            log(f"order failed status={r.status} body={body}")
            log_event("ERROR", f"order failed {r.status}")
            return None
        return await r.json()

async def jupiter_execute(session: aiohttp.ClientSession, order: dict):
    if wallet is None:
        return None
    tx_b64 = order.get("transaction")
    request_id = order.get("requestId")
    if not tx_b64 or not request_id:
        log("order missing transaction/requestId")
        return None

    raw_tx = VersionedTransaction.from_bytes(base64.b64decode(tx_b64))
    signed_tx = VersionedTransaction(raw_tx.message, [wallet])
    signed_b64 = base64.b64encode(bytes(signed_tx)).decode()

    payload = {
        "signedTransaction": signed_b64,
        "requestId": request_id,
    }
    if order.get("lastValidBlockHeight") is not None:
        payload["lastValidBlockHeight"] = order["lastValidBlockHeight"]

    async with session.post(
        "https://api.jup.ag/swap/v2/execute",
        json=payload,
        headers={"Content-Type": "application/json", **jup_headers()},
        timeout=30,
    ) as r:
        if r.status >= 400:
            body = await r.text()
            log(f"execute failed status={r.status} body={body}")
            log_event("ERROR", f"execute failed {r.status}")
            return None
        return await r.json()

def _now():
    return datetime.utcnow().isoformat()

async def buy(session: aiohttp.ClientSession, mint: str, score: float):
    existing = {p["token"] for p in engine["positions"]}
    if mint in existing:
        return False

    if len(engine["positions"]) >= CONFIG["MAX_OPEN_POSITIONS"]:
        return False

    entry_price = await get_price(session, mint)
    if not entry_price:
        return False

    if CONFIG["MODE"] != "REAL":
        engine["last_trade"] = f"PAPER BUY {mint[:8]}"
        engine["stats"]["buys"] += 1
        pos = {
            "token": mint,
            "amount": CONFIG["BUY_AMOUNT_SOL"],
            "score": score,
            "entry_price": entry_price,
            "opened_at": _now(),
            "pnl": 0.0,
            "peak_price": entry_price,
            "scaled_out": False,
        }
        engine["positions"].append(pos)
        upsert_position(mint, CONFIG["BUY_AMOUNT_SOL"], 0, entry_price, score, "OPEN")
        insert_trade("BUY", mint, CONFIG["BUY_AMOUNT_SOL"], 0, entry_price, None, None, None, "ENTRY", CONFIG["MODE"], None, "FILLED")
        log(f"🧪 PAPER BUY {mint[:8]} score={score}")
        log_event("INFO", f"PAPER BUY {mint[:8]}")
        return True

    if wallet is None:
        log("⚠️ Skip buy: PRIVATE_KEY not set")
        return False

    atoms = int(CONFIG["BUY_AMOUNT_SOL"] * 1e9)
    order = await jupiter_order(session, SOL, mint, atoms)
    if not order:
        return False

    result = await jupiter_execute(session, order)
    if not result:
        return False

    txid = result.get("signature") or result.get("txid")
    engine["last_trade"] = f"REAL BUY {mint[:8]}"
    engine["stats"]["buys"] += 1
    pos = {
        "token": mint,
        "amount": CONFIG["BUY_AMOUNT_SOL"],
        "score": score,
        "entry_price": entry_price,
        "opened_at": _now(),
        "pnl": 0.0,
        "peak_price": entry_price,
        "scaled_out": False,
    }
    engine["positions"].append(pos)
    upsert_position(mint, CONFIG["BUY_AMOUNT_SOL"], 0, entry_price, score, "OPEN")
    insert_trade("BUY", mint, CONFIG["BUY_AMOUNT_SOL"], 0, entry_price, None, None, None, "ENTRY", CONFIG["MODE"], txid, "FILLED")
    log(f"⚡ REAL BUY {mint[:8]} result={result}")
    log_event("INFO", f"REAL BUY {mint[:8]}")
    return True

async def sell(session: aiohttp.ClientSession, mint: str, reason: str, fraction: float = 1.0):
    pos = next((p for p in engine["positions"] if p["token"] == mint), None)
    if not pos:
        return False

    current_price = await get_price(session, mint)
    entry_price = pos.get("entry_price") or 0
    pnl = None
    pnl_pct = None
    if current_price and entry_price:
        pnl_pct = (current_price - entry_price) / entry_price
        pnl = pos.get("amount", 0) * pnl_pct

    if CONFIG["MODE"] != "REAL":
        engine["last_trade"] = f"PAPER SELL {mint[:8]} ({reason})"
        engine["stats"]["sells"] += 1
        if fraction >= 0.999:
            engine["positions"] = [p for p in engine["positions"] if p["token"] != mint]
            close_position(mint)
        else:
            pos["amount"] = round(pos["amount"] * (1 - fraction), 8)
            pos["scaled_out"] = True
            upsert_position(mint, pos["amount"], 0, pos["entry_price"], pos["score"], "OPEN")
        insert_trade("SELL", mint, pos.get("amount"), 0, entry_price, current_price, pnl, pnl_pct, reason, CONFIG["MODE"], None, "FILLED")
        log(f"🔴 PAPER SELL {mint[:8]} reason={reason} fraction={fraction}")
        return True

    if wallet is None:
        log("⚠️ Skip sell: PRIVATE_KEY not set")
        return False

    sell_amount_sol = pos.get("amount", CONFIG["BUY_AMOUNT_SOL"]) * fraction
    atoms = int(sell_amount_sol * 1e9)
    order = await jupiter_order(session, mint, SOL, atoms)
    if not order:
        return False

    result = await jupiter_execute(session, order)
    if not result:
        return False

    txid = result.get("signature") or result.get("txid")
    engine["last_trade"] = f"REAL SELL {mint[:8]} ({reason})"
    engine["stats"]["sells"] += 1

    if fraction >= 0.999:
        engine["positions"] = [p for p in engine["positions"] if p["token"] != mint]
        close_position(mint)
    else:
        pos["amount"] = round(pos["amount"] * (1 - fraction), 8)
        pos["scaled_out"] = True
        upsert_position(mint, pos["amount"], 0, pos["entry_price"], pos["score"], "OPEN")

    insert_trade("SELL", mint, sell_amount_sol, 0, entry_price, current_price, pnl, pnl_pct, reason, CONFIG["MODE"], txid, "FILLED")
    log(f"🔴 REAL SELL {mint[:8]} reason={reason} fraction={fraction} result={result}")
    log_event("INFO", f"REAL SELL {mint[:8]} {reason}")
    return True

async def parse_tx(session: aiohttp.ClientSession, sig: str):
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransaction",
        "params": [sig, {"encoding": "jsonParsed"}]
    }
    try:
        async with session.post(RPC, json=payload, timeout=10) as r:
            tx = await r.json()
        if not tx.get("result"):
            return None
        meta = tx["result"]["meta"]
        msg = tx["result"]["transaction"]["message"]
        wallet_addr = msg["accountKeys"][0]["pubkey"]
        for b in meta.get("postTokenBalances", []):
            mint = b.get("mint")
            amt = b.get("uiTokenAmount", {}).get("uiAmount")
            if mint and amt is not None and mint != SOL:
                return mint, wallet_addr, float(amt)
    except Exception:
        return None
    return None

async def manage_positions(session: aiohttp.ClientSession):
    while True:
        await asyncio.sleep(3)
        for pos in list(engine["positions"]):
            mint = pos["token"]
            entry = pos.get("entry_price") or 0
            if not entry:
                continue

            current = await get_price(session, mint)
            if not current:
                continue

            pnl = (current - entry) / entry
            pos["pnl"] = round(pnl, 4)
            pos["peak_price"] = max(pos.get("peak_price", entry), current)

            # Scale out at first target
            if pnl >= CONFIG["TAKE_PROFIT"] and not pos.get("scaled_out"):
                await sell(session, mint, "SCALE_OUT", CONFIG["SCALE_OUT_PCT"])
                continue

            # Trailing stop after peak
            peak = pos.get("peak_price", current)
            if peak > entry:
                drawdown_from_peak = (current - peak) / peak
                if drawdown_from_peak <= -CONFIG["TRAILING_STOP"]:
                    await sell(session, mint, "TRAILING_STOP", 1.0)
                    continue

            if pnl <= CONFIG["STOP_LOSS"]:
                await sell(session, mint, "STOP_LOSS", 1.0)
                blacklist.add(mint)
                continue

            # Time exit
            try:
                opened_at = datetime.fromisoformat(pos["opened_at"])
                hold_sec = (datetime.utcnow() - opened_at).total_seconds()
            except Exception:
                hold_sec = 0

            if hold_sec >= CONFIG["MAX_HOLD_SEC"]:
                await sell(session, mint, "TIME_EXIT", 1.0)

async def refresh_trade_history_loop():
    while True:
        try:
            engine["trade_history"] = fetch_recent_trades(50)
        except Exception:
            pass
        await asyncio.sleep(5)

async def bot_loop():
    engine["mode"] = CONFIG["MODE"]
    log("🚀 Multi-position + DB bot started")

    async with aiohttp.ClientSession() as session:
        asyncio.create_task(sync_positions_loop(session))
        asyncio.create_task(manage_positions(session))
        asyncio.create_task(refresh_trade_history_loop())

        async with session.ws_connect(WS) as ws:
            await ws.send_json({"jsonrpc":"2.0","id":1,"method":"logsSubscribe","params":["all"]})
            async for msg in ws:
                try:
                    data = msg.json()
                    if data.get("method") != "logsNotification":
                        continue

                    sig = data["params"]["result"]["value"]["signature"]
                    parsed = await parse_tx(session, sig)
                    if not parsed:
                        continue

                    mint, trader_wallet, amount = parsed
                    if mint in blacklist:
                        continue

                    bucket = flow_cache.setdefault(mint, [])
                    bucket.append(amount)
                    if len(bucket) < 6:
                        continue

                    flow = sum(bucket[-10:])
                    wallets = len(bucket)
                    momentum = sum(bucket[-3:])

                    engine["stats"]["signals"] += 1

                    local_score = round(local_ai_score(flow, wallets, momentum), 3)
                    payload = {
                        "flow": flow,
                        "wallets": wallets,
                        "momentum": momentum,
                        "mint": mint,
                    }
                    tq_score = await turboquant_score(session, payload)
                    final_score = round(tq_score if tq_score is not None else local_score, 3)

                    engine["last_signal"] = f"{mint[:8]} | score={final_score} | flow={round(flow,2)} | wallets={wallets}"
                    log(f"📡 signal {mint[:8]} score={final_score} flow={round(flow,2)} wallets={wallets}")

                    if flow < CONFIG["MIN_FLOW"]:
                        continue

                    if wallets < CONFIG["MIN_WALLETS"]:
                        continue

                    if rug_score(flow, wallets, momentum) > CONFIG["RUG_THRESHOLD"]:
                        continue

                    if final_score < 0.7:
                        continue

                    await buy(session, mint, final_score)

                except Exception as e:
                    engine["stats"]["errors"] += 1
                    log(f"loop error: {e}")
                    log_event("ERROR", f"loop error: {e}")
                    continue
