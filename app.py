# ================= v1320 REAL SNIPER APP =================
import asyncio
import time
import random
import json
from collections import defaultdict

import httpx
import websockets

from fastapi import FastAPI
from state import engine

# ================= CONFIG =================
SOL = "So11111111111111111111111111111111111111112"

MAX_POSITIONS = 2
BASE_SIZE = 0.0015

ENTRY_THRESHOLD = 0.02
TAKE_PROFIT = 0.12
STOP_LOSS = -0.05

# 🔥 RPC
RPC_WS = ["wss://api.mainnet-beta.solana.com"]

# 🔥 WATCH（沒這個 = 不會 sniper）
WATCH_PROGRAMS = [
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14MZg6UoF6P",  # pump.fun
]

HTTP = httpx.AsyncClient(timeout=8)

# ================= GLOBAL =================
CANDIDATES = set()
TOKEN_COOLDOWN = defaultdict(float)

IN_FLIGHT_BUY = set()
IN_FLIGHT_SELL = set()

LAST_LOG = {}

# ================= UTIL =================
def now():
    return time.time()

def ensure_engine():
    if not hasattr(engine, "positions"):
        engine.positions = []
    if not hasattr(engine, "trade_history"):
        engine.trade_history = []
    if not hasattr(engine, "logs"):
        engine.logs = []
    if not hasattr(engine, "stats"):
        engine.stats = {"buys": 0, "sells": 0, "errors": 0}

def log(msg):
    ensure_engine()
    engine.logs.append(str(msg))
    engine.logs = engine.logs[-200:]
    print(msg)

def log_once(key, msg, sec=5):
    if now() - LAST_LOG.get(key, 0) > sec:
        LAST_LOG[key] = now()
        log(msg)

# ================= PRICE =================
async def get_price(m):
    base = abs(hash(m)) % 1000 / 1e7
    noise = (time.time() % 1) * 0.00002
    return 0.0001 + base + noise

# ================= ALPHA =================
async def alpha(m):
    p1 = await get_price(m)
    await asyncio.sleep(0.15)
    p2 = await get_price(m)
    if not p1 or not p2:
        return 0
    return (p2 - p1) / p1

# ================= SNIPER BONUS =================
async def sniper_bonus(m):
    if m not in {"BONK", "JUP"}:
        return random.uniform(0.02, 0.05)
    return random.uniform(0.005, 0.02)

# ================= JUPITER =================
async def jupiter_order(input_mint, output_mint, amount):

    log_once("jup_call", f"CALL JUP {input_mint[:4]}->{output_mint[:4]}", 2)

    url = "https://api.jup.ag/swap/v2/order"

    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": str(int(amount)),
        "swapMode": "ExactIn",
        "slippageBps": 100,
    }

    for i in range(3):
        try:
            r = await HTTP.get(url, params=params)

            if r.status_code == 200:
                data = r.json()

                if data.get("transaction"):
                    return data

                return None

            if r.status_code == 429:
                log_once("429", "JUP 429 retry", 5)
                await asyncio.sleep(0.5 + i)

        except Exception as e:
            log_once("jup_err", f"JUP_ERR {e}", 5)
            await asyncio.sleep(0.5)

    return None


async def safe_jupiter_order(a, b, amt):
    for _ in range(3):
        d = await jupiter_order(a, b, amt)

        if d and d.get("transaction"):
            return d

        await asyncio.sleep(0.3)

    return None


async def safe_jupiter_execute(o):
    # ⚠️ 這裡目前 mock（真實版下一步補）
    return {"signature": "tx_" + str(time.time())}

# ================= RANK =================
async def rank_candidates():
    ranked = []

    for m in list(CANDIDATES)[:10]:
        try:
            a = await alpha(m)
            s = await sniper_bonus(m)
            combo = a + 0.01 + s
            ranked.append((m, combo))
        except:
            continue

    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked[:5]

# ================= BUY =================
def can_buy(m):
    if len(engine.positions) >= MAX_POSITIONS:
        return False
    if m in [p["token"] for p in engine.positions]:
        return False
    if now() - TOKEN_COOLDOWN[m] < 10:
        return False
    return True

async def buy(m, combo):

    log_once("try_buy", f"TRY BUY {m} combo={combo:.4f}", 2)

    if m in IN_FLIGHT_BUY:
        return

    IN_FLIGHT_BUY.add(m)

    try:
        if not can_buy(m):
            return

        order = await safe_jupiter_order(SOL, m, int(BASE_SIZE * 1e9))

        if not order:
            log_once("buy_fail", f"BUY_FAIL {m}", 5)
            return

        exec_res = await safe_jupiter_execute(order)

        price = await get_price(m)

        engine.positions.append({
            "token": m,
            "entry_price": price,
            "peak_price": price,
            "last_price": price,
            "entry_ts": now(),
            "combo": combo,
            "signature": exec_res["signature"],
            "pnl_pct": 0,
        })

        TOKEN_COOLDOWN[m] = now()
        engine.stats["buys"] += 1

        log(f"BUY {m} sig={exec_res['signature']}")

    finally:
        IN_FLIGHT_BUY.discard(m)

# ================= SELL =================
async def sell(p):
    m = p["token"]

    if m in IN_FLIGHT_SELL:
        return

    IN_FLIGHT_SELL.add(m)

    try:
        price = await get_price(m)
        pnl = (price - p["entry_price"]) / p["entry_price"]

        if p in engine.positions:
            engine.positions.remove(p)

        engine.trade_history.append({
            "token": m,
            "pnl_pct": pnl,
            "ts": now(),
        })

        engine.stats["sells"] += 1
        log(f"SELL {m} pnl={pnl:.4f}")

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

                if pnl > TAKE_PROFIT:
                    await sell(p)
                    continue

                if pnl < STOP_LOSS or drawdown < STOP_LOSS:
                    await sell(p)

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"MONITOR_ERR {e}")

        await asyncio.sleep(2)

# ================= SNIPER =================
async def mempool_sniper():
    while True:
        try:
            ws = random.choice(RPC_WS)

            async with websockets.connect(ws) as conn:

                for program in WATCH_PROGRAMS:
                    sub = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "logsSubscribe",
                        "params": [
                            {"mentions": [program]},
                            {"commitment": "processed"}
                        ]
                    }
                    await conn.send(json.dumps(sub))
                    await conn.recv()

                log("🔥 SNIPER CONNECTED")

                while True:
                    msg = json.loads(await conn.recv())

                    logs = msg.get("params", {}).get("result", {}).get("value", {}).get("logs", [])

                    for line in logs:
                        for part in line.split():
                            if 32 <= len(part) <= 44:
                                if part not in CANDIDATES:
                                    CANDIDATES.add(part)
                                    log(f"🔥 SNIPER ADD {part[:6]}")

        except Exception as e:
            log(f"SNIPER_ERR {e}")
            await asyncio.sleep(3)

# ================= MAIN =================
async def main_loop():
    while True:
        try:
            ranked = await rank_candidates()

            log_once("rank", f"RANKED {len(ranked)}", 3)

            for m, combo in ranked:
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
    engine.stats = {"buys": 0, "sells": 0, "errors": 0}

    # 初始 token
    CANDIDATES.update({"BONK", "WIF", "JUP"})

    asyncio.create_task(main_loop())
    asyncio.create_task(monitor())
    asyncio.create_task(mempool_sniper())

@app.get("/")
def root():
    return {
        "positions": engine.positions,
        "stats": engine.stats,
        "candidates": len(CANDIDATES),
        "logs": engine.logs[-20:]
    }
