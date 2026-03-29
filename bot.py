# ================= v1301_REAL_MARKET_BOT (完整補齊版 - 2026.03) =================
import asyncio
import time
import random
from collections import defaultdict

import httpx

from state import engine
from mempool import mempool_stream

# ================= CONFIG =================
SOL = "So11111111111111111111111111111111111111112"

MAX_POSITION_SOL = 0.0025
MIN_POSITION_SOL = 0.001
MAX_POSITIONS = 5

PUMP_API = "https://frontend-api.pump.fun/coins/latest"

SEED_TOKENS = {SOL}

HTTP = httpx.AsyncClient(timeout=10)

# ================= INIT =================
if not hasattr(engine, "positions"):
    engine.positions = []

engine.logs = getattr(engine, "logs", [])
engine.trade_history = getattr(engine, "trade_history", [])
engine.capital = getattr(engine, "capital", 1.0)
engine.sol_balance = getattr(engine, "sol_balance", 1.0)
engine.loss_streak = getattr(engine, "loss_streak", 0)
engine.last_trade = getattr(engine, "last_trade", "")
engine.last_signal = getattr(engine, "last_signal", "")
engine.running = True
engine.mode = getattr(engine, "mode", "PAPER")
engine.bot_ok = True
engine.bot_error = ""

engine.stats = getattr(engine, "stats", {
    "signals": 0, "buys": 0, "sells": 0, "errors": 0
})

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

# ================= STATE =================
CANDIDATES = set()
TOKEN_COOLDOWN = defaultdict(float)
ALPHA_CACHE = {}
PRICE_CACHE = {}
LAST_PUMP_ERROR = {"code": None, "ts": 0.0}
LAST_UNIVERSE_REFRESH = 0.0

# ================= UTIL =================
def log(msg: str) -> None:
    engine.logs.append(msg)
    engine.logs = engine.logs[-200:]
    print(f"[BOT] {msg}")

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
            params={"inputMint": mint, "outputMint": SOL, "amount": "1000000"},
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
            params={"inputMint": SOL, "outputMint": mint, "amount": "10000000"},
        )
        if r.status_code != 200:
            return 0, 1.0
        data = r.json()
        out = int(data.get("outAmount", 0) or 0)
        impact = float(data.get("priceImpactPct", 1) or 1)
        return out, impact
    except Exception:
        return 0, 1.0

# ================= FILTER =================
async def liquidity_ok(mint: str) -> bool:
    out, impact = await get_liquidity_and_impact(mint)
    return out > 5000 and impact < 0.40   # 提高流動性門檻

async def anti_rug(mint: str) -> bool:
    try:
        r = await HTTP.get(
            "https://lite-api.jup.ag/swap/v1/quote",
            params={"inputMint": mint, "outputMint": SOL, "amount": "1000000"},
        )
        if r.status_code != 200:
            return False
        return int(r.json().get("outAmount", 0) or 0) > 0
    except Exception:
        return False

# ================= TOKEN SOURCES =================
async def pump_scanner():
    while True:
        try:
            r = await HTTP.get(PUMP_API)
            if r.status_code != 200:
                ts = now()
                if LAST_PUMP_ERROR.get("code") != r.status_code:
                    log(f"PUMP_HTTP_{r.status_code}")
                    LAST_PUMP_ERROR["code"] = r.status_code
                await asyncio.sleep(8)
                continue

            data = r.json()
            added = 0
            for c in data[:15]:
                mint = c.get("mint") if isinstance(c, dict) else None
                if valid_mint(mint) and mint not in CANDIDATES:
                    CANDIDATES.add(mint)
                    added += 1
            if added > 0:
                log(f"PUMP_OK +{added}")
        except Exception as e:
            engine.stats["errors"] += 1
            log(f"PUMP_ERR {str(e)[:60]}")
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
    engine.candidate_count = len(CANDIDATES)
    log(f"UNIVERSE_REFRESH total={len(CANDIDATES)}")

# ================= ALPHA & ENGINE =================
async def alpha_engine(mint: str) -> float:
    try:
        p1 = await get_price(mint)
        await asyncio.sleep(0.08)
        p2 = await get_price(mint)
        if not p1 or not p2 or p1 <= 0:
            return 0.01
        momentum = (p2 - p1) / p1
        return max(0.0, min(momentum * 0.6, 0.08))
    except:
        return 0.01

def pick_engine(alpha: float) -> str:
    if alpha > 0.07:
        return "sniper"
    if alpha > 0.03:
        return random.choices(["stable", "degen", "sniper"], weights=[0.2, 0.5, 0.3])[0]
    
    # 強制轉 list，避免 slice 錯誤
    weights_list = [ENGINE_ALLOCATOR["stable"], ENGINE_ALLOCATOR["degen"], ENGINE_ALLOCATOR["sniper"]]
    return random.choices(["stable", "degen", "sniper"], weights=weights_list)[0]

def update_allocator():
    engine.engine_stats = ENGINE_STATS.copy()
    engine.engine_allocator = ENGINE_ALLOCATOR.copy()

# ================= EXEC =================
def can_buy(mint: str) -> bool:
    if len(engine.positions) >= MAX_POSITIONS:
        return False
    if any(p.get("token") == mint for p in engine.positions):
        return False
    if now() - TOKEN_COOLDOWN[mint] < 10:
        return False
    return True

async def buy(mint: str, alpha: float) -> bool:
    eng = pick_engine(alpha)
    if not can_buy(mint):
        return False

    price = await get_price(mint)
    if not price or price <= 0:
        return False

    s = MAX_POSITION_SOL * min(1.0, alpha * 8)
    amount = s / price

    engine.positions.append({
        "token": mint,
        "amount": amount,
        "entry_price": price,
        "last_price": price,
        "peak_price": price,
        "pnl_pct": 0.0,
        "engine": eng,
        "alpha": alpha,
    })

    TOKEN_COOLDOWN[mint] = now()
    engine.stats["buys"] += 1
    engine.last_trade = f"BUY {mint[:8]}"
    log(f"BUY {mint[:8]} eng={eng} alpha={alpha:.4f} size={s:.6f}")
    return True

async def sell(p: dict) -> None:
    price = await get_price(p["token"])
    if not price:
        return
    entry = p.get("entry_price", price)
    pnl_pct = (price - entry) / entry if entry > 0 else 0.0

    eng = p.get("engine", "sniper")
    ENGINE_STATS[eng]["trades"] += 1
    if pnl_pct > 0:
        ENGINE_STATS[eng]["wins"] += 1

    engine.positions = [pos for pos in engine.positions if pos["token"] != p["token"]]
    engine.stats["sells"] += 1
    engine.last_trade = f"SELL {p['token'][:8]}"
    log(f"SELL {p['token'][:8]} pnl%={pnl_pct*100:+.2f}")

# ================= MONITOR =================
async def monitor_positions():
    while True:
        try:
            for p in list(engine.positions):
                price = await get_price(p["token"])
                if not price:
                    continue
                entry = p.get("entry_price", 0.0)
                pnl_pct = (price - entry) / entry if entry > 0 else 0.0
                p["last_price"] = price
                p["pnl_pct"] = pnl_pct

                if pnl_pct >= 0.18 or pnl_pct <= -0.09:
                    await sell(p)
        except Exception as e:
            log(f"MONITOR ERROR: {e}")
        await asyncio.sleep(6)

# ================= MAIN LOOP =================
async def main():
    log("🚀 v1301_REAL_MARKET_BOT 已啟動 (PAPER MODE)")
    asyncio.create_task(pump_scanner())
    asyncio.create_task(mempool_stream(handle_mempool))
    asyncio.create_task(monitor_positions())

    while True:
        try:
            await refresh_token_universe()
            update_allocator()

            for mint in list(CANDIDATES)[:10]:
                if await liquidity_ok(mint) and await anti_rug(mint):
                    alpha = await alpha_engine(mint)
                    if alpha > 0.012:
                        await buy(mint, alpha)

            await asyncio.sleep(7)
        except Exception as e:
            log(f"MAIN LOOP ERROR: {e}")
            await asyncio.sleep(10)

# ================= 給 FastAPI 使用的入口 =================
async def bot_loop():
    """解決 import bot_loop 的關鍵函數"""
    try:
        await main()
    except Exception as e:
        log(f"bot_loop 異常終止: {e}")
        engine.bot_error = str(e)

if __name__ == "__main__":
    asyncio.run(main())
