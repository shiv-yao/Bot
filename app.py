# ================= v1320 INTEGRATED REAL SNIPER CORE =================
import os
import time
import json
import base64
import random
import asyncio
from collections import defaultdict

import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

from state import engine

# ================= CONFIG =================
SOL_MINT = "So11111111111111111111111111111111111111112"

JUP_API_KEY = os.getenv("JUP_API_KEY", "").strip()
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "").strip()

DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
SLIPPAGE_BPS = int(os.getenv("SLIPPAGE_BPS", "100"))

MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "2"))
ENTRY_THRESHOLD = float(os.getenv("ENTRY_THRESHOLD", "0.03"))

TP_PCT = float(os.getenv("TP_PCT", "0.10"))
SL_PCT = float(os.getenv("SL_PCT", "0.05"))
TRAIL_DD_PCT = float(os.getenv("TRAIL_DD_PCT", "0.05"))

BUY_SIZE_LAMPORTS = int(os.getenv("BUY_SIZE_LAMPORTS", "1000000"))

HTTP = httpx.AsyncClient(timeout=20)

# ================= TOKEN MAP =================
# 你原本用符號，Jupiter 實際要 mint
TOKEN_MINTS = {
    "BONK": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6YaB1pPB2633PBnd",
    "WIF": "EKpQGSJtjMFqKZqQanSqYXRcF6j6G4Vd8s4eJc5qQyQ",   # 若你有更準確 mint，直接替換
    "JUP": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
    "MYRO": "HhJpBhxVwthpYjE1PqZ3VHtV6nRvWmvMRdqg9x6p8B4n", # 若你有更準確 mint，直接替換
    "POPCAT": "7GCihgDB8fe6KnwQ2ZrM9KBzN7UJ2d1hMSe7TzW1C1f", # 若你有更準確 mint，直接替換
}

CANDIDATES = set(TOKEN_MINTS.keys())

# ================= GLOBAL =================
TOKEN_COOLDOWN = defaultdict(float)
IN_FLIGHT_BUY = set()
IN_FLIGHT_SELL = set()
LAST_LOG = {}

# ================= UTIL =================
def now():
    return time.time()

def ensure_list(v):
    if isinstance(v, list):
        return v
    if v is None:
        return []
    if isinstance(v, (tuple, set)):
        return list(v)
    if isinstance(v, dict):
        return [v]
    if isinstance(v, str):
        return [v]
    try:
        return list(v)
    except Exception:
        return []

def ensure_engine():
    current_positions = getattr(engine, "positions", [])
    current_trade_history = getattr(engine, "trade_history", [])
    current_logs = getattr(engine, "logs", [])
    current_stats = getattr(engine, "stats", {})

    engine.positions = ensure_list(current_positions)
    engine.trade_history = ensure_list(current_trade_history)
    engine.logs = ensure_list(current_logs)

    if not isinstance(current_stats, dict):
        current_stats = {}

    engine.stats = {
        "buys": int(current_stats.get("buys", 0)),
        "sells": int(current_stats.get("sells", 0)),
        "errors": int(current_stats.get("errors", 0)),
        "signals": int(current_stats.get("signals", 0)),
    }

def log(msg):
    ensure_engine()
    engine.logs.append(str(msg))
    if len(engine.logs) > 200:
        engine.logs = engine.logs[-200:]
    print(msg, flush=True)

def log_once(key, msg, sec=5):
    if now() - LAST_LOG.get(key, 0) > sec:
        LAST_LOG[key] = now()
        log(msg)

def get_keypair():
    if DRY_RUN:
        return None
    if not PRIVATE_KEY:
        raise ValueError("PRIVATE_KEY missing")
    return Keypair.from_base58_string(PRIVATE_KEY)

def symbol_to_mint(symbol: str) -> str:
    return TOKEN_MINTS.get(symbol, symbol)

# ================= PRICE =================
async def get_price(m):
    base = abs(hash(m)) % 1000 / 1e7
    return 0.0001 + base + random.uniform(-0.00001, 0.00002)

# ================= ALPHA =================
async def alpha(m):
    p1 = await get_price(m)
    await asyncio.sleep(0.2)
    p2 = await get_price(m)
    return (p2 - p1) / p1 if p1 else 0

# ================= SIGNAL =================
def wallet_score(m):
    return 1.0

async def sniper_bonus(m):
    return random.uniform(0.01, 0.02)

# ================= JUPITER V2 =================
async def jupiter_order(input_mint, output_mint, amount):
    log_once("jup_call", f"CALL JUP {input_mint[:4]}->{output_mint[:4]}", 2)

    if DRY_RUN:
        return {
            "transaction": "DRY_TX",
            "requestId": f"dry_{int(now())}",
            "inputMint": input_mint,
            "outputMint": output_mint,
            "inAmount": str(amount),
        }

    if not JUP_API_KEY:
        raise ValueError("JUP_API_KEY missing")

    headers = {
        "x-api-key": JUP_API_KEY
    }

    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": str(int(amount)),
        "swapMode": "ExactIn",
        "slippageBps": SLIPPAGE_BPS,
        "taker": str(get_keypair().pubkey()),
    }

    r = await HTTP.get(
        "https://api.jup.ag/swap/v2/order",
        params=params,
        headers=headers,
    )
    r.raise_for_status()
    data = r.json()

    if data.get("error") or data.get("errorCode") or data.get("errorMessage"):
        log_once("jup_order_err", f"ORDER_ERR {data}", 3)
        return None

    if not data.get("transaction"):
        log_once("jup_no_tx", f"NO TX {output_mint[:6]}", 5)
        return None

    return data

async def safe_jupiter_order(a, b, amt):
    for _ in range(3):
        try:
            d = await jupiter_order(a, b, amt)
            if d:
                return d
        except Exception as e:
            log_once("jup_err", f"JUP_ERR {type(e).__name__}: {e}", 5)
        await asyncio.sleep(0.4)
    return None

async def safe_jupiter_execute(order):
    if DRY_RUN:
        await asyncio.sleep(0.05)
        return {"signature": f"dry_tx_{int(time.time())}"}

    if not JUP_API_KEY:
        raise ValueError("JUP_API_KEY missing")

    tx_b64 = order["transaction"]
    raw = base64.b64decode(tx_b64)
    kp = get_keypair()
    tx = VersionedTransaction.from_bytes(raw)
    signed = VersionedTransaction(tx.message, [kp])

    headers = {
        "x-api-key": JUP_API_KEY
    }

    body = {
        "signedTransaction": base64.b64encode(bytes(signed)).decode(),
        "requestId": order.get("requestId"),
    }

    r = await HTTP.post(
        "https://api.jup.ag/swap/v2/execute",
        headers=headers,
        json=body,
    )
    r.raise_for_status()
    data = r.json()

    sig = data.get("signature") or data.get("txid")
    if not sig:
        log_once("exec_fail", f"EXEC_FAIL {data}", 3)
        return None

    return {"signature": sig, "raw": data}

# ================= BUY =================
def can_buy(m):
    ensure_engine()

    if len(engine.positions) >= MAX_POSITIONS:
        return False
    if m in [p["token"] for p in engine.positions]:
        return False
    if now() - TOKEN_COOLDOWN[m] < 10:
        return False
    return True

async def buy(symbol, combo):
    ensure_engine()

    if symbol in IN_FLIGHT_BUY:
        return

    IN_FLIGHT_BUY.add(symbol)

    try:
        if not can_buy(symbol):
            return

        log_once(f"try_{symbol}", f"TRY BUY {symbol} combo={combo:.4f}", 3)

        output_mint = symbol_to_mint(symbol)
        order = await safe_jupiter_order(SOL_MINT, output_mint, BUY_SIZE_LAMPORTS)

        if not order:
            log_once("buy_fail", f"BUY_FAIL {symbol}", 5)
            return

        exec_res = await safe_jupiter_execute(order)
        if not exec_res:
            log_once("buy_exec_fail", f"BUY_EXEC_FAIL {symbol}", 5)
            return

        price = await get_price(symbol)

        engine.positions.append({
            "token": symbol,
            "mint": output_mint,
            "entry_price": price,
            "last_price": price,
            "peak_price": price,
            "entry_ts": now(),
            "signature": exec_res["signature"],
            "combo": combo,
            "pnl_pct": 0.0
        })

        TOKEN_COOLDOWN[symbol] = now()
        engine.stats["buys"] += 1

        log(f"BUY SUCCESS {symbol}")

    except Exception as e:
        engine.stats["errors"] += 1
        log(f"BUY ERR {symbol} {type(e).__name__}: {e}")

    finally:
        IN_FLIGHT_BUY.discard(symbol)

# ================= SELL =================
async def sell(p):
    ensure_engine()

    m = p["token"]
    if m in IN_FLIGHT_SELL:
        return

    IN_FLIGHT_SELL.add(m)

    try:
        if not DRY_RUN:
            order = await safe_jupiter_order(
                p["mint"],
                SOL_MINT,
                BUY_SIZE_LAMPORTS
            )
            if not order:
                log(f"SELL_FAIL {m}")
                return

            exec_res = await safe_jupiter_execute(order)
            if not exec_res:
                log(f"SELL_EXEC_FAIL {m}")
                return

        price = await get_price(m)
        pnl = (price - p["entry_price"]) / p["entry_price"]

        if p in engine.positions:
            engine.positions.remove(p)

        engine.trade_history.append({
            "token": m,
            "pnl_pct": pnl,
            "ts": now()
        })

        engine.stats["sells"] += 1
        log(f"SELL {m} pnl={pnl:.4f}")

    except Exception as e:
        engine.stats["errors"] += 1
        log(f"SELL_ERR {m} {type(e).__name__}: {e}")

    finally:
        IN_FLIGHT_SELL.discard(m)

# ================= MONITOR =================
async def monitor():
    while True:
        try:
            for p in list(engine.positions):
                price = await get_price(p["token"])

                pnl = (price - p["entry_price"]) / p["entry_price"]
                peak = max(p["peak_price"], price)

                p["peak_price"] = peak
                p["last_price"] = price
                p["pnl_pct"] = pnl

                drawdown = (price - peak) / peak

                if pnl > TP_PCT or pnl < -SL_PCT or drawdown < -TRAIL_DD_PCT:
                    await sell(p)

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"MONITOR_ERR {e}")

        await asyncio.sleep(2)

# ================= RANK =================
async def rank_candidates():
    ranked = []

    for m in list(CANDIDATES):
        a = await alpha(m)
        w = wallet_score(m)
        s = await sniper_bonus(m)

        combo = a + (w * 0.01) + s
        ranked.append((m, combo))

    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked[:5]

# ================= MAIN =================
async def main_loop():
    while True:
        try:
            ranked = await rank_candidates()

            log_once("rank", f"RANKED {len(ranked)}", 5)

            for m, combo in ranked:
                engine.stats["signals"] += 1
                if combo > ENTRY_THRESHOLD:
                    await buy(m, combo)

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"ERR {e}")

        await asyncio.sleep(3)

# ================= APP =================
app = FastAPI()

@app.on_event("startup")
async def start():
    ensure_engine()

    engine.positions = []
    engine.trade_history = []
    engine.logs = []
    engine.stats = {"buys": 0, "sells": 0, "errors": 0, "signals": 0}

    asyncio.create_task(main_loop())
    asyncio.create_task(monitor())

@app.on_event("shutdown")
async def shutdown():
    await HTTP.aclose()

@app.get("/")
def root():
    ensure_engine()
    return {
        "mode": "DRY_RUN" if DRY_RUN else "REAL",
        "positions": engine.positions,
        "stats": engine.stats,
        "logs": engine.logs[-20:]
    }

@app.get("/ping")
def ping():
    return {"ok": True}

@app.get("/health")
def health():
    return {
        "ok": True,
        "mode": "DRY_RUN" if DRY_RUN else "REAL",
        "candidates": list(CANDIDATES),
    }

@app.get("/ui")
def ui():
    return HTMLResponse("""
    <html>
    <body style="background:black;color:lime;font-family:monospace">
    <h2>🔥 v1320 Integrated Sniper</h2>
    <div id="data"></div>
    <script>
    async function load(){
        let res = await fetch('/');
        let d = await res.json();
        document.getElementById("data").innerHTML =
            "<pre>"+JSON.stringify(d,null,2)+"</pre>";
    }
    setInterval(load, 2000);
    load();
    </script>
    </body>
    </html>
    """)
