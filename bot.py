# ================= v1301_REAL_MARKET_BOT =================

import asyncio
import time
import random
from collections import defaultdict

import httpx

from state import engine
from mempool import mempool_stream

# ================= CONFIG =================

SOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT = "Es9vMFrzaCERmJfrF4H2FYD6hF4n7hH3UX77PGD5Y8v"
JUP = "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"

MAX_POSITION_SOL = 0.0025
MIN_POSITION_SOL = 0.001
MAX_POSITIONS = 5

PUMP_API = "https://frontend-api.pump.fun/coins/latest"

SEED_TOKENS = {
    SOL,
    USDC,
    USDT,
    JUP,
}

HTTP = httpx.AsyncClient(timeout=10)

# ================= INIT =================

if not hasattr(engine, "positions"):
    engine.positions = []

engine.logs = []
engine.trade_history = []
engine.capital = 1.0
engine.sol_balance = getattr(engine, "sol_balance", 1.0)
engine.loss_streak = 0
engine.last_trade = ""
engine.last_signal = ""
engine.running = True
engine.mode = getattr(engine, "mode", "PAPER")
engine.bot_ok = True
engine.bot_error = ""

engine.stats = {
    "signals": 0,
    "buys": 0,
    "sells": 0,
    "errors": 0,
}

# ================= ENGINE =================

ENGINE_STATS = {
    "stable": {"pnl": 0.0, "trades": 0, "wins": 0},
    "degen": {"pnl": 0.0, "trades": 0, "wins": 0},
    "sniper": {"pnl": 0.0, "trades": 0, "wins": 0},
}

ENGINE_ALLOCATOR = {
    "stable": 0.4,
    "degen": 0.4,
    "sniper": 0.2,
}

ALPHA_MEMORY = {
    "stable": [],
    "degen": [],
    "sniper": [],
}

# ================= STATE =================

CANDIDATES = set()
TOKEN_COOLDOWN = defaultdict(float)
ALPHA_CACHE = {}
PRICE_CACHE = {}
LAST_PRICE = {}
LAST_PUMP_ERROR = {"code": None, "ts": 0.0}
LAST_UNIVERSE_REFRESH = 0.0

# ================= UTIL =================

def log(msg: str) -> None:
    engine.logs.append(msg)
    engine.logs = engine.logs[-200:]
    print(msg)

def now() -> float:
    return time.time()

def valid_mint(m: str) -> bool:
    return isinstance(m, str) and 32 <= len(m) <= 44

# ================= MARKET DATA =================

async def get_price(mint: str):
    cached = PRICE_CACHE.get(mint)
    if cached and now() - cached[1] < 3:
        return cached[0]

    try:
        r = await HTTP.get(
            "https://lite-api.jup.ag/swap/v1/quote",
            params={
                "inputMint": mint,
                "outputMint": SOL,
                "amount": "1000000",
            },
        )
        if r.status_code != 200:
            return None

        data = r.json()
        out_amount = int(data.get("outAmount", 0) or 0)
        price = (out_amount / 1e9) / 1_000_000 if out_amount > 0 else None

        PRICE_CACHE[mint] = (price, now())
        return price

    except Exception:
        engine.stats["errors"] += 1
        return None

async def get_liquidity_and_impact(mint: str):
    try:
        r = await HTTP.get(
            "https://lite-api.jup.ag/swap/v1/quote",
            params={
                "inputMint": SOL,
                "outputMint": mint,
                "amount": "10000000",
            },
        )
        if r.status_code != 200:
            return 0, 1.0

        data = r.json()
        out = int(data.get("outAmount", 0) or 0)
        impact = float(data.get("priceImpactPct", 1) or 1)
        return out, impact
    except Exception:
        return 0, 1.0

# ================= TOKEN SOURCES =================

async def pump_scanner():
    while True:
        try:
            r = await HTTP.get(PUMP_API)

            if r.status_code != 200:
                ts = now()
                if LAST_PUMP_ERROR["code"] != r.status_code or ts - LAST_PUMP_ERROR["ts"] > 60:
                    log(f"PUMP_HTTP_{r.status_code}")
                    LAST_PUMP_ERROR["code"] = r.status_code
                    LAST_PUMP_ERROR["ts"] = ts
                await asyncio.sleep(8)
                continue

            text = r.text.strip()
            if not text:
                log("PUMP_EMPTY")
                await asyncio.sleep(8)
                continue

            try:
                data = r.json()
            except Exception:
                log(f"PUMP_BAD_JSON {text[:80]}")
                await asyncio.sleep(8)
                continue

            if not isinstance(data, list):
                log(f"PUMP_BAD_SHAPE {type(data).__name__}")
                await asyncio.sleep(8)
                continue

            added = 0
            for c in data[:20]:
                mint = c.get("mint") if isinstance(c, dict) else None
                if valid_mint(mint):
                    if mint not in CANDIDATES:
                        added += 1
                    CANDIDATES.add(mint)

            if added > 0:
                log(f"PUMP_OK +{added}")

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"PUMP_ERR {str(e)[:80]}")

        await asyncio.sleep(8)

async def handle_mempool(e: dict):
    mint = e.get("mint")
    if valid_mint(mint):
        CANDIDATES.add(mint)

async def refresh_token_universe():
    global LAST_UNIVERSE_REFRESH

    if now() - LAST_UNIVERSE_REFRESH < 120:
        return

    LAST_UNIVERSE_REFRESH = now()

    CANDIDATES.update(SEED_TOKENS)

    for p in engine.positions:
        mint = p.get("token")
        if valid_mint(mint):
            CANDIDATES.add(mint)

    log(f"UNIVERSE_REFRESH total={len(CANDIDATES)}")

# ================= ALPHA =================

async def momentum(mint: str) -> float:
    p1 = await get_price(mint)
    await asyncio.sleep(0.10)
    p2 = await get_price(mint)

    if not p1 or not p2 or p1 <= 0:
        return 0.0

    return (p2 - p1) / p1

async def micro(mint: str) -> float:
    p1 = await get_price(mint)
    await asyncio.sleep(0.05)
    p2 = await get_price(mint)

    if not p1 or not p2 or p1 <= 0:
        return 0.0

    return (p2 - p1) / p1

async def volume_surge(mint: str) -> float:
    p = await get_price(mint)
    if not p:
        return 0.0

    prev = LAST_PRICE.get(mint, p)
    LAST_PRICE[mint] = p

    return abs(p - prev) / prev if prev > 0 else 0.0

async def liquidity_score(mint: str) -> float:
    out, impact = await get_liquidity_and_impact(mint)

    if out <= 0:
        return 0.0

    liq_term = min(out / 100000, 2.0)
    impact_penalty = max(0.0, impact - 0.10) * 2.0
    return max(0.0, liq_term - impact_penalty)

async def alpha_engine(mint: str) -> float:
    cached = ALPHA_CACHE.get(mint)
    if cached and now() - cached[1] < 4:
        return cached[0]

    m = await momentum(mint)
    mic = await micro(mint)
    vol = await volume_surge(mint)
    liq = await liquidity_score(mint)

    raw_score = (
        m * 0.45 +
        mic * 0.20 +
        vol * 0.20 +
        liq * 0.15
    )

    # 關鍵修正：避免 alpha 永遠衝到超高，讓三引擎有分流空間
    score = max(0.0, min(raw_score, 0.08))

    ALPHA_CACHE[mint] = (score, now())
    return score

# ================= FILTER =================

async def liquidity_ok(mint: str) -> bool:
    out, impact = await get_liquidity_and_impact(mint)
    if out <= 0:
        return False
    return impact < 0.40

async def anti_rug(mint: str) -> bool:
    try:
        r = await HTTP.get(
            "https://lite-api.jup.ag/swap/v1/quote",
            params={
                "inputMint": mint,
                "outputMint": SOL,
                "amount": "1000000",
            },
        )
        if r.status_code != 200:
            return False
        return int(r.json().get("outAmount", 0) or 0) > 0
    except Exception:
        return False

# ================= ENGINE LOGIC =================

def update_allocator() -> None:
    weights = {}

    for k, v in ENGINE_STATS.items():
        if v["trades"] == 0:
            weights[k] = 1.0
        else:
            win = v["wins"] / max(v["trades"], 1)
            weights[k] = (v["pnl"] + 0.001) * win

    total = sum(abs(v) for v in weights.values()) + 1e-9
    for k in weights:
        weights[k] = abs(weights[k]) / total

    ENGINE_ALLOCATOR.update(weights)

def get_alpha_edge(engine_name: str, alpha: float) -> float:
    mem = ALPHA_MEMORY[engine_name]

    if not mem:
        return 1.0

    sim = [p for a, p in mem if abs(a - alpha) < 0.02]
    if not sim:
        return 1.0

    avg = sum(sim) / len(sim)
    return max(0.5, min(2.0, 1 + avg * 5))

def pick_engine(alpha: float) -> str:
    # 真正分流版本
    if alpha > 0.07:
        return "sniper"

    if alpha > 0.03:
        return random.choices(
            ["stable", "degen", "sniper"],
            weights=[0.2, 0.5, 0.3],
            k=1,
        )[0]

    return random.choices(
        ["stable", "degen", "sniper"],
        weights=list(ENGINE_ALLOCATOR.values()),
        k=1,
    )[0]

def size(alpha: float, eng: str) -> float:
    base = MAX_POSITION_SOL * min(1.0, alpha * 6)
    alloc = ENGINE_ALLOCATOR[eng]
    edge = get_alpha_edge(eng, alpha)

    s = base * alloc * edge

    if engine.loss_streak >= 3:
        s *= 0.5

    return max(MIN_POSITION_SOL, min(MAX_POSITION_SOL, s))

# ================= EXEC =================

def can_buy(mint: str) -> bool:
    if len(engine.positions) >= MAX_POSITIONS:
        return False
    if any(p["token"] == mint for p in engine.positions):
        return False
    if now() - TOKEN_COOLDOWN[mint] < 10:
        return False
    return True

async def buy(mint: str, alpha: float) -> bool:
    eng = pick_engine(alpha)

    if not can_buy(mint):
        return False

    price = await get_price(mint)
    if not price:
        return False

    s = size(alpha, eng)
    amount = s / price

    engine.positions.append(
        {
            "token": mint,
            "amount": amount,
            "entry_price": price,
            "last_price": price,
            "peak_price": price,
            "pnl_pct": 0.0,
            "engine": eng,
            "alpha": alpha,
        }
    )

    TOKEN_COOLDOWN[mint] = now()

    engine.stats["buys"] += 1
    engine.last_trade = f"BUY {mint[:8]}"

    log(f"BUY {mint[:8]} eng={eng} alpha={round(alpha,4)} size={round(s,6)}")
    return True

async def sell(p: dict) -> None:
    price = await get_price(p["token"])
    if
