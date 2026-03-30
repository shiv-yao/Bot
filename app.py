import os
import base64
import asyncio
import random
from collections import defaultdict

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse, HTMLResponse
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from solana.rpc.async_api import AsyncClient

from state import engine

RPCS = [
    os.getenv("RPC_1", "https://api.mainnet-beta.solana.com"),
    os.getenv("RPC_2", "https://rpc.ankr.com/solana"),
]

PRIVATE_KEY = os.getenv("PRIVATE_KEY", "").strip()

SLIPPAGE_BPS = int(os.getenv("SLIPPAGE_BPS", "300"))
BASE_SIZE_SOL = float(os.getenv("BASE_SIZE", "0.02"))
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "3"))
TAKE_PROFIT = float(os.getenv("TP", "0.30"))
STOP_LOSS = float(os.getenv("SL", "0.15"))
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

SOL_MINT = "So11111111111111111111111111111111111111112"

rpc_index = 0
rpc_client = AsyncClient(RPCS[rpc_index], timeout=15)
token_cooldown = defaultdict(float)
last_log_time = {}

app = FastAPI(title="Sniper Bot")


def ensure_list(value):
    if isinstance(value, list):
        return value
    if value is None:
        return []
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)
    if isinstance(value, dict):
        return [value]
    if isinstance(value, str):
        return [value]
    try:
        return list(value)
    except Exception:
        return []


def init_engine():
    current_positions = getattr(engine, "positions", [])
    engine.positions = ensure_list(current_positions)

    current_logs = getattr(engine, "logs", [])
    engine.logs = ensure_list(current_logs)

    current_stats = getattr(engine, "stats", None)
    if not isinstance(current_stats, dict):
        current_stats = {}

    engine.stats = {
        "signals": int(current_stats.get("signals", 0)),
        "buys": int(current_stats.get("buys", 0)),
        "sells": int(current_stats.get("sells", 0)),
        "errors": int(current_stats.get("errors", 0)),
    }

    if not hasattr(engine, "running"):
        engine.running = True

    engine.mode = "DRY_RUN" if DRY_RUN else "REAL"


def log_once(key: str, msg: str, sec: float = 5.0):
    now = asyncio.get_event_loop().time()
    if now - last_log_time.get(key, 0) >= sec:
        print(msg, flush=True)

        if not isinstance(getattr(engine, "logs", None), list):
            engine.logs = ensure_list(getattr(engine, "logs", []))

        engine.logs.append(msg)

        if len(engine.logs) > 200:
            engine.logs = engine.logs[-200:]

        last_log_time[key] = now


def get_keypair() -> Keypair:
    if not PRIVATE_KEY and not DRY_RUN:
        raise ValueError("PRIVATE_KEY is empty")
    return Keypair.from_base58_string(PRIVATE_KEY)


def get_client() -> AsyncClient:
    global rpc_client
    return rpc_client


async def rotate_client():
    global rpc_index, rpc_client
    try:
        await rpc_client.close()
    except Exception:
        pass
    rpc_index = (rpc_index + 1) % len(RPCS)
    rpc_client = AsyncClient(RPCS[rpc_index], timeout=15)
    log_once("rpc_rotate", f"RPC ROTATE -> {RPCS[rpc_index]}", 1)


async def jup_swap_tx(input_mint: str, output_mint: str, amount_sol: float) -> str | None:
    lamports = int(amount_sol * 1_000_000_000)

    async with httpx.AsyncClient(timeout=20) as client:
        quote_resp = await client.get(
            "https://quote-api.jup.ag/v6/quote",
            params={
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": lamports,
                "slippageBps": SLIPPAGE_BPS,
            },
        )
        quote_resp.raise_for_status()
        quote_data = quote_resp.json()

        routes = quote_data.get("data", [])
        if not routes:
            log_once(f"no_route_{output_mint}", f"NO ROUTE {output_mint}", 2)
            return None

        route = routes[0]

        if DRY_RUN:
            return "DRY_TX"

        swap_body = {
            "route": route,
            "userPublicKey": str(get_keypair().pubkey()),
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
        }

        swap_resp = await client.post(
            "https://quote-api.jup.ag/v6/swap",
            json=swap_body,
        )
        swap_resp.raise_for_status()
        swap_data = swap_resp.json()

        tx_b64 = swap_data.get("swapTransaction")
        if not tx_b64:
            log_once(f"no_tx_{output_mint}", f"NO TX {output_mint}", 2)
            return None

        return tx_b64


async def send_tx(tx_base64: str) -> str | None:
    if DRY_RUN:
        fake_sig = f"DRYRUN_{random.randint(100000, 999999)}"
        log_once("dry_send", f"DRY SENT {fake_sig}", 1)
        return fake_sig

    try:
        keypair = get_keypair()
        raw_tx = base64.b64decode(tx_base64)
        tx = VersionedTransaction.from_bytes(raw_tx)
        signed_tx = VersionedTransaction(tx.message, [keypair])

        client = get_client()
        resp = await client.send_raw_transaction(bytes(signed_tx))
        sig = str(resp.value)
        log_once("tx_sent", f"SENT {sig}", 1)

        for _ in range(10):
            status = await client.get_signature_statuses([sig])
            value = status.value[0]
            if value is not None and value.confirmation_status is not None:
                log_once("tx_ok", f"CONFIRMED {sig}", 1)
                return sig
            await asyncio.sleep(1)

        log_once("tx_timeout", f"TIMEOUT {sig}", 1)
        return sig

    except Exception as e:
        log_once("send_fail", f"SEND FAIL {type(e).__name__}: {e}", 1)
        await rotate_client()
        return None


async def buy_token(token_mint: str, score: float):
    now = asyncio.get_event_loop().time()

    if len(engine.positions) >= MAX_POSITIONS:
        return

    if token_cooldown[token_mint] > now:
        return

    log_once(f"buy_try_{token_mint}", f"TRY BUY {token_mint} score={score:.4f}", 1)

    try:
        tx_b64 = await jup_swap_tx(SOL_MINT, token_mint, BASE_SIZE_SOL)
        if not tx_b64:
            log_once(f"buy_fail_{token_mint}", f"BUY FAIL {token_mint}", 1)
            token_cooldown[token_mint] = now + 20
            return

        sig = await send_tx(tx_b64)
        if not sig:
            log_once(f"buy_send_fail_{token_mint}", f"BUY SEND FAIL {token_mint}", 1)
            token_cooldown[token_mint] = now + 20
            return

        entry_price = random.uniform(0.8, 1.2)
        engine.positions.append(
            {
                "token": token_mint,
                "size_sol": BASE_SIZE_SOL,
                "entry_score": score,
                "entry_price": entry_price,
                "last_price": entry_price,
                "tx": sig,
            }
        )
        engine.stats["buys"] += 1
        log_once(f"buy_ok_{token_mint}", f"BUY OK {token_mint}", 1)
        token_cooldown[token_mint] = now + 30

    except Exception as e:
        engine.stats["errors"] += 1
        log_once(f"buy_err_{token_mint}", f"BUY ERR {token_mint} {type(e).__name__}: {e}", 1)


async def sell_position(pos: dict):
    token_mint = pos["token"]

    try:
        if DRY_RUN:
            sig = f"DRYSELL_{random.randint(100000, 999999)}"
            log_once(f"sell_ok_{token_mint}", f"SELL OK {token_mint} {sig}", 1)
        else:
            tx_b64 = await jup_swap_tx(token_mint, SOL_MINT, pos["size_sol"])
            if not tx_b64:
                log_once(f"sell_fail_{token_mint}", f"SELL FAIL {token_mint}", 1)
                return

            sig = await send_tx(tx_b64)
            if not sig:
                log_once(f"sell_send_fail_{token_mint}", f"SELL SEND FAIL {token_mint}", 1)
                return

        if pos in engine.positions:
            engine.positions.remove(pos)

        engine.stats["sells"] += 1

    except Exception as e:
        engine.stats["errors"] += 1
        log_once(f"sell_err_{token_mint}", f"SELL ERR {token_mint} {type(e).__name__}: {e}", 1)


def mock_score() -> float:
    return random.random()


async def bot_loop():
    while True:
        try:
            if not engine.running:
                await asyncio.sleep(2)
                continue

            watchlist = [
                "DezXAZ8z7PnrnRJjz3wXBoRgixCa6YaB1pPB2633PBnd",
                "7xKXtg2CWmCzM39jN6iYH2sQPL6V2wRk5vK4wJ8pump",
            ]

            ranked = []
            for mint in watchlist:
                s = mock_score()
                engine.stats["signals"] += 1
                ranked.append((mint, s))

            ranked.sort(key=lambda x: x[1], reverse=True)

            for mint, score in ranked:
                if score > 0.6:
                    await buy_token(mint, score)

            await asyncio.sleep(3)

        except Exception as e:
            engine.stats["errors"] += 1
            log_once("bot_loop_err", f"BOT LOOP ERR {type(e).__name__}: {e}", 1)
            await asyncio.sleep(3)


async def risk_loop():
    while True:
        try:
            for pos in list(engine.positions):
                drift = random.uniform(-0.08, 0.12)
                pos["last_price"] = max(0.0001, pos["last_price"] * (1 + drift))
                pnl = (pos["last_price"] - pos["entry_price"]) / pos["entry_price"]

                if pnl >= TAKE_PROFIT:
                    log_once(f"tp_{pos['token']}", f"TAKE PROFIT {pos['token']} pnl={pnl:.3f}", 1)
                    await sell_position(pos)
                elif pnl <= -STOP_LOSS:
                    log_once(f"sl_{pos['token']}", f"STOP LOSS {pos['token']} pnl={pnl:.3f}", 1)
                    await sell_position(pos)

            await asyncio.sleep(5)

        except Exception as e:
            engine.stats["errors"] += 1
            log_once("risk_loop_err", f"RISK LOOP ERR {type(e).__name__}: {e}", 1)
            await asyncio.sleep(5)


@app.on_event("startup")
async def startup():
    init_engine()
    asyncio.create_task(bot_loop())
    asyncio.create_task(risk_loop())
    log_once("startup", f"STARTUP mode={engine.mode}", 1)


@app.get("/")
async def root():
    return JSONResponse(
        {
            "mode": engine.mode,
            "positions": engine.positions,
            "stats": engine.stats,
            "logs": engine.logs[-50:] if isinstance(engine.logs, list) else [],
        }
    )


@app.get("/health")
async def health():
    return {"ok": True, "mode": engine.mode}


@app.get("/ui")
async def ui():
    return HTMLResponse(
        """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Sniper Bot</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
</head>
<body style="background:#000;color:#0f0;font-family:monospace;padding:16px;">
  <h2>🔥 SNIPER BOT</h2>
  <div id="data">loading...</div>
  <script>
    async function load() {
      const res = await fetch('/');
      const data = await res.json();
      document.getElementById('data').innerHTML =
        '<pre>' + JSON.stringify(data, null, 2) + '</pre>';
    }
    setInterval(load, 2000);
    load();
  </script>
</body>
</html>
"""
    )
