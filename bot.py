# ================= v1309 FULL MERGED BOT =================
import asyncio
import time
import random
from collections import defaultdict

import httpx

from state import engine
from mempool import mempool_stream
from wallet_tracker import extract_wallets_from_mints, track_wallet_behavior

# ================= CONFIG =================
SOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wGk3Q3k5Jp3x"
USDT = "Es9vMFrzaCERm7w7z7y7v4JgJ6pG6fQ5gYdExgkt1Py"
BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6YaB1pPB263kzwc"
JUP = "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"

STATIC_UNIVERSE = {SOL, USDC, USDT, BONK, JUP}
FALLBACK_TOKENS = set(STATIC_UNIVERSE)

MAX_POSITION_SOL = 0.0025
MIN_POSITION_SOL = 0.001
MAX_POSITIONS = 5

PUMP_API = "https://frontend-api.pump.fun/coins/latest"
JUP_TOKENS_API = "https://token.jup.ag/all"

HTTP = httpx.AsyncClient(timeout=10.0)

# ================= AI =================
AI_PARAMS = {
    "entry_threshold": 0.002,
    "size_multiplier": 1.0,
    "trailing_stop": 0.08,
}

# ================= STATE =================
CANDIDATES = set()
TOKEN_COOLDOWN = defaultdict(float)
PRICE_CACHE = {}
LAST_LOG_TS = {}

# ================= WALLET GRAPH =================
WALLET_GRAPH = {}
WALLET_SCORE = {}

# ================= UTIL =================
def now(): return time.time()

def valid_mint(m): return isinstance(m, str) and 32 <= len(m) <= 44

def log(msg):
    engine.logs.append(str(msg))
    engine.logs = engine.logs[-300:]
    print("[BOT]", msg)

def log_once(k, msg, cd=60):
    if now() - LAST_LOG_TS.get(k, 0) > cd:
        LAST_LOG_TS[k] = now()
        log(msg)

# ================= HTTP =================
async def http_get(url, params=None):
    try:
        r = await HTTP.get(url, params=params)
        if r.status_code != 200:
            return None
        return r.json()
    except:
        return None

# ================= MARKET =================
async def get_price(m):
    if m in PRICE_CACHE and now() - PRICE_CACHE[m][1] < 4:
        return PRICE_CACHE[m][0]

    data = await http_get(
        "https://lite-api.jup.ag/swap/v1/quote",
        {"inputMint": m, "outputMint": SOL, "amount": "1000000"}
    )

    if not data:
        return None

    out = int(data.get("outAmount", 0))
    price = (out / 1e9) / 1_000_000 if out > 0 else None
    PRICE_CACHE[m] = (price, now())
    return price

async def liquidity_ok(m):
    data = await http_get(
        "https://lite-api.jup.ag/swap/v1/quote",
        {"inputMint": SOL, "outputMint": m, "amount": "10000000"}
    )
    return data and int(data.get("outAmount", 0)) > 5000

async def anti_rug(m):
    data = await http_get(
        "https://lite-api.jup.ag/swap/v1/quote",
        {"inputMint": m, "outputMint": SOL, "amount": "1000000"}
    )
    return data and int(data.get("outAmount", 0)) > 0

# ================= WALLET =================
async def build_wallet_graph():
    RPC = "https://api.mainnet-beta.solana.com"
    try:
        wallets = await extract_wallets_from_mints(RPC, list(CANDIDATES)[-20:])
        behaviors = await track_wallet_behavior(RPC, wallets)

        for b in behaviors:
            w = b["wallet"]
            tokens = b["tokens"]

            WALLET_GRAPH[w] = tokens
            WALLET_SCORE[w] = min(len(tokens) / 10, 1.5)

    except Exception as e:
        log_once("wallet_graph", str(e))

def wallet_score(m):
    score = 1.0
    for w, tokens in WALLET_GRAPH.items():
        if m in tokens:
            score += WALLET_SCORE.get(w, 0)
    return min(score, 3.0)

# ================= SNIPER =================
SNIPER_CACHE = set()

async def sniper(m):
    if m in SNIPER_CACHE:
        return 0
    SNIPER_CACHE.add(m)

    if m not in STATIC_UNIVERSE:
        return 0.01 + random.random() * 0.01
    return 0

# ================= ALPHA =================
async def alpha(m):
    p1 = await get_price(m)
    await asyncio.sleep(1)
    p2 = await get_price(m)

    if not p1 or not p2:
        return 0
    return (p2 - p1) / p1

# ================= RANK =================
async def rank_candidates():
    ranked = []

    for m in list(CANDIDATES):
        try:
            a = await alpha(m)
            w = wallet_score(m)
            s = await sniper(m)

            combo = a + (w * 0.01) + s
            ranked.append((m, combo, a, w))

        except:
            continue

    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked[:15]

# ================= AI LOOP =================
async def ai_loop():
    while True:
        try:
            trades = engine.trade_history[-30:]
            if trades:
                avg = sum(t["pnl_pct"] for t in trades) / len(trades)

                if avg > 0:
                    AI_PARAMS["entry_threshold"] *= 0.98
                    AI_PARAMS["size_multiplier"] *= 1.05
                else:
                    AI_PARAMS["entry_threshold"] *= 1.05
                    AI_PARAMS["size_multiplier"] *= 0.95

        except Exception as e:
            log_once("ai", str(e))

        await asyncio.sleep(10)

# ================= EXEC =================
def can_buy(m):
    if m in {SOL, USDC, USDT}:
        return False
    if len(engine.positions) >= MAX_POSITIONS:
        return False
    if any(p["token"] == m for p in engine.positions):
        return False
    if now() - TOKEN_COOLDOWN[m] < 30:
        return False
    return True

async def buy(m, a, combo):
    if not can_buy(m):
        return

    price = await get_price(m)
    if not price:
        return

    size = MAX_POSITION_SOL * max(0.2, combo * 8) * AI_PARAMS["size_multiplier"]

    if combo > 0.02:
        size *= 1.3

    size = max(MIN_POSITION_SOL, min(size, MAX_POSITION_SOL))

    engine.positions.append({
        "token": m,
        "entry_price": price,
        "last_price": price,
        "peak_price": price,
        "pnl_pct": 0,
        "amount": size / price,
        "engine": "degen",
        "alpha": a,
        "entry_ts": now()
    })

    TOKEN_COOLDOWN[m] = now()
    engine.stats["buys"] += 1
    log(f"BUY {m[:6]} combo={combo:.4f}")

async def sell(p):
    price = await get_price(p["token"])
    if not price:
        return

    pnl = (price - p["entry_price"]) / p["entry_price"]

    engine.positions.remove(p)

    engine.trade_history.append({
        "token": p["token"],
        "pnl_pct": pnl,
        "ts": now()
    })

    engine.stats["sells"] += 1
    log(f"SELL {p['token'][:6]} pnl={pnl:.4f}")

# ================= MONITOR =================
async def monitor():
    while True:
        for p in list(engine.positions):
            price = await get_price(p["token"])
            if not price:
                continue

            pnl = (price - p["entry_price"]) / p["entry_price"]
            peak = max(p["peak_price"], price)
            p["peak_price"] = peak
            p["pnl_pct"] = pnl

            drawdown = (price - peak) / peak if peak else 0
            stop = -AI_PARAMS["trailing_stop"]

            if pnl < stop or drawdown < stop:
                await sell(p)

        await asyncio.sleep(6)

# ================= SOURCES =================
async def pump():
    while True:
        data = await http_get(PUMP_API)
        if isinstance(data, list):
            for c in data[:20]:
                m = c.get("mint")
                if valid_mint(m):
                    CANDIDATES.add(m)
        await asyncio.sleep(10)

async def jup():
    while True:
        data = await http_get(JUP_TOKENS_API)
        if isinstance(data, list):
            for t in data[:50]:
                CANDIDATES.add(t.get("address"))
        await asyncio.sleep(120)

async def mempool_loop():
    while True:
        try:
            await mempool_stream(lambda e: CANDIDATES.add(e.get("mint")))
        except:
            await asyncio.sleep(5)

# ================= MAIN =================
async def main():
    log("🚀 v1309 FULL BOT START")

    for m in FALLBACK_TOKENS:
        CANDIDATES.add(m)

    asyncio.create_task(pump())
    asyncio.create_task(jup())
    asyncio.create_task(mempool_loop())
    asyncio.create_task(monitor())
    asyncio.create_task(ai_loop())

    while True:
        try:
            await build_wallet_graph()

            ranked = await rank_candidates()

            for m, combo, a, w in ranked:
                engine.stats["signals"] += 1

                if not await liquidity_ok(m):
                    continue

                if not await anti_rug(m):
                    continue

                engine.last_signal = f"{m[:6]} a={a:.4f} w={w:.2f} c={combo:.4f}"

                if combo > AI_PARAMS["entry_threshold"]:
                    await buy(m, a, combo)

            await asyncio.sleep(6)

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"ERR {e}")
            await asyncio.sleep(5)

# ================= ENTRY =================
async def bot_loop():
    await main()
