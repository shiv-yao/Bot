# ================= v1302_HARDENED_REAL_MARKET_BOT =================
import asyncio
import time
import random
import traceback
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

HTTP = httpx.AsyncClient(timeout=10.0)

# ================= SAFE HELPERS =================
def ensure_list(x):
    return x if isinstance(x, list) else []

def ensure_dict(x):
    return x if isinstance(x, dict) else {}

def ensure_float(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default

def ensure_int(x, default=0):
    try:
        return int(x)
    except Exception:
        return default

def safe_slice(x, n=10):
    if isinstance(x, (list, tuple)):
        return list(x[:n])
    return []

def safe_get(d, key, default=None):
    if isinstance(d, dict):
        return d.get(key, default)
    return default

def as_json_or_none(resp: httpx.Response):
    try:
        return resp.json()
    except Exception:
        return None

def now() -> float:
    return time.time()

def valid_mint(m: str) -> bool:
    return isinstance(m, str) and 32 <= len(m) <= 44

def repair_engine_state():
    engine.positions = ensure_list(getattr(engine, "positions", []))
    engine.logs = ensure_list(getattr(engine, "logs", []))
    engine.trade_history = ensure_list(getattr(engine, "trade_history", []))

    raw_stats = getattr(engine, "stats", {})
    raw_stats = ensure_dict(raw_stats)
    engine.stats = {
        "signals": ensure_int(raw_stats.get("signals", 0)),
        "buys": ensure_int(raw_stats.get("buys", 0)),
        "sells": ensure_int(raw_stats.get("sells", 0)),
        "errors": ensure_int(raw_stats.get("errors", 0)),
    }

    engine.capital = ensure_float(getattr(engine, "capital", 1.0), 1.0)
    engine.sol_balance = ensure_float(getattr(engine, "sol_balance", 1.0), 1.0)
    engine.loss_streak = ensure_int(getattr(engine, "loss_streak", 0), 0)
    engine.last_trade = str(getattr(engine, "last_trade", ""))
    engine.last_signal = str(getattr(engine, "last_signal", ""))
    engine.running = bool(getattr(engine, "running", True))
    engine.mode = str(getattr(engine, "mode", "PAPER"))
    engine.bot_ok = bool(getattr(engine, "bot_ok", True))
    engine.bot_error = str(getattr(engine, "bot_error", ""))
    engine.candidate_count = ensure_int(getattr(engine, "candidate_count", 0), 0)

def log(msg: str) -> None:
    repair_engine_state()
    try:
        engine.logs.append(str(msg))
        engine.logs = engine.logs[-200:]
    except Exception:
        engine.logs = [str(msg)]
    print(f"[BOT] {msg}")

# ================= INIT =================
repair_engine_state()

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

# ================= HTTP SAFE =================
async def http_get_json(url: str, params=None):
    try:
        r = await HTTP.get(url, params=params)
        if r.status_code != 200:
            return None, r.status_code
        data = as_json_or_none(r)
        return data, 200
    except Exception:
        return None, None

# ================= MARKET DATA =================
async def get_price(mint: str):
    if not valid_mint(mint):
        return None

    cached = PRICE_CACHE.get(mint)
    if isinstance(cached, tuple) and len(cached) == 2 and now() - cached[1] < 3:
        return cached[0]

    data, status = await http_get_json(
        "https://lite-api.jup.ag/swap/v1/quote",
        params={"inputMint": mint, "outputMint": SOL, "amount": "1000000"},
    )
    if status != 200 or not isinstance(data, dict):
        return None

    out_amount = ensure_int(data.get("outAmount", 0), 0)
    price = (out_amount / 1e9) / 1_000_000 if out_amount > 0 else None

    PRICE_CACHE[mint] = (price, now())
    return price

async def get_liquidity_and_impact(mint: str):
    if not valid_mint(mint):
        return 0, 1.0

    data, status = await http_get_json(
        "https://lite-api.jup.ag/swap/v1/quote",
        params={"inputMint": SOL, "outputMint": mint, "amount": "10000000"},
    )
    if status != 200 or not isinstance(data, dict):
        return 0, 1.0

    out_amount = ensure_int(data.get("outAmount", 0), 0)
    impact = ensure_float(data.get("priceImpactPct", 1.0), 1.0)
    return out_amount, impact

# ================= FILTER =================
async def liquidity_ok(mint: str) -> bool:
    out_amount, impact = await get_liquidity_and_impact(mint)
    return out_amount > 5000 and impact < 0.40

async def anti_rug(mint: str) -> bool:
    if not valid_mint(mint):
        return False

    data, status = await http_get_json(
        "https://lite-api.jup.ag/swap/v1/quote",
        params={"inputMint": mint, "outputMint": SOL, "amount": "1000000"},
    )
    if status != 200 or not isinstance(data, dict):
        return False

    return ensure_int(data.get("outAmount", 0), 0) > 0

# ================= TOKEN SOURCES =================
async def pump_scanner():
    while True:
        try:
            data, status = await http_get_json(PUMP_API)
            if status != 200:
                if LAST_PUMP_ERROR.get("code") != status:
                    log(f"PUMP_HTTP_{status}")
                    LAST_PUMP_ERROR["code"] = status
                    LAST_PUMP_ERROR["ts"] = now()
                await asyncio.sleep(8)
                continue

            if not isinstance(data, list):
                log("PUMP_BAD_PAYLOAD")
                await asyncio.sleep(8)
                continue

            added = 0
            for c in safe_slice(data, 15):
                if not isinstance(c, dict):
                    continue
                mint = c.get("mint")
                if valid_mint(mint) and mint not in CANDIDATES:
                    CANDIDATES.add(mint)
                    added += 1

            if added > 0:
                log(f"PUMP_OK +{added}")

        except Exception as e:
            repair_engine_state()
            engine.stats["errors"] += 1
            log(f"PUMP_ERR {str(e)[:80]}")
        await asyncio.sleep(8)

async def handle_mempool(event: dict):
    try:
        if not isinstance(event, dict):
            return
        mint = event.get("mint")
        if valid_mint(mint):
            CANDIDATES.add(mint)
    except Exception as e:
        repair_engine_state()
        engine.stats["errors"] += 1
        log(f"MEMPOOL_HANDLE_ERR {str(e)[:80]}")

async def mempool_runner():
    while True:
        try:
            await mempool_stream(handle_mempool)
        except Exception as e:
            repair_engine_state()
            engine.stats["errors"] += 1
            log(f"MEMPOOL_STREAM_ERR {str(e)[:80]}")
            await asyncio.sleep(5)

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
        cache_key = f"a:{mint}"
        cached = ALPHA_CACHE.get(cache_key)
        if isinstance(cached, tuple) and len(cached) == 2 and now() - cached[1] < 5:
            return cached[0]

        p1 = await get_price(mint)
        await asyncio.sleep(0.08)
        p2 = await get_price(mint)

        if not p1 or not p2 or p1 <= 0:
            alpha = 0.01
        else:
            momentum = (p2 - p1) / p1
            alpha = max(0.0, min(momentum * 0.6, 0.08))

        ALPHA_CACHE[cache_key] = (alpha, now())
        return alpha
    except Exception:
        return 0.01

def pick_engine(alpha: float) -> str:
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
        weights=[0.4, 0.4, 0.2],
        k=1,
    )[0]

def update_allocator():
    engine.engine_stats = {
        k: {
            "pnl": ensure_float(v.get("pnl", 0.0)),
            "trades": ensure_int(v.get("trades", 0)),
            "wins": ensure_int(v.get("wins", 0)),
        }
        for k, v in ENGINE_STATS.items()
    }
    engine.engine_allocator = dict(ENGINE_ALLOCATOR)

# ================= EXEC =================
def normalize_position(p: dict):
    if not isinstance(p, dict):
        return None

    token = p.get("token")
    if not valid_mint(token):
        return None

    return {
        "token": token,
        "amount": ensure_float(p.get("amount", 0.0), 0.0),
        "entry_price": ensure_float(p.get("entry_price", 0.0), 0.0),
        "last_price": ensure_float(p.get("last_price", 0.0), 0.0),
        "peak_price": ensure_float(p.get("peak_price", 0.0), 0.0),
        "pnl_pct": ensure_float(p.get("pnl_pct", 0.0), 0.0),
        "engine": str(p.get("engine", "stable")),
        "alpha": ensure_float(p.get("alpha", 0.0), 0.0),
    }

def repair_positions():
    repair_engine_state()
    fixed = []
    for p in engine.positions:
        np = normalize_position(p)
        if np is not None:
            fixed.append(np)
    engine.positions = fixed[:MAX_POSITIONS]

def can_buy(mint: str) -> bool:
    repair_positions()

    if not valid_mint(mint):
        return False
    if len(engine.positions) >= MAX_POSITIONS:
        return False
    if any(isinstance(p, dict) and p.get("token") == mint for p in engine.positions):
        return False
    if now() - TOKEN_COOLDOWN[mint] < 10:
        return False
    return True

async def buy(mint: str, alpha: float) -> bool:
    repair_positions()

    eng = pick_engine(alpha)
    if not can_buy(mint):
        return False

    price = await get_price(mint)
    if not price or price <= 0:
        return False

    size_sol = MAX_POSITION_SOL * min(1.0, max(0.2, alpha * 8))
    size_sol = max(MIN_POSITION_SOL, min(size_sol, MAX_POSITION_SOL))
    amount = size_sol / price if price > 0 else 0.0

    pos = {
        "token": mint,
        "amount": amount,
        "entry_price": price,
        "last_price": price,
        "peak_price": price,
        "pnl_pct": 0.0,
        "engine": eng,
        "alpha": alpha,
    }

    engine.positions.append(pos)
    engine.positions = engine.positions[:MAX_POSITIONS]

    TOKEN_COOLDOWN[mint] = now()
    engine.stats["buys"] += 1
    engine.last_trade = f"BUY {mint[:8]}"
    engine.last_signal = f"{eng}:{alpha:.4f}"

    log(f"BUY {mint[:8]} eng={eng} alpha={alpha:.4f} size={size_sol:.6f}")
    return True

async def sell(p: dict) -> None:
    repair_positions()

    token = p.get("token")
    if not valid_mint(token):
        return

    price = await get_price(token)
    if not price:
        return

    entry = ensure_float(p.get("entry_price", price), price)
    pnl_pct = (price - entry) / entry if entry > 0 else 0.0
    eng = str(p.get("engine", "sniper"))

    if eng not in ENGINE_STATS:
        eng = "stable"

    ENGINE_STATS[eng]["trades"] += 1
    ENGINE_STATS[eng]["pnl"] += pnl_pct
    if pnl_pct > 0:
        ENGINE_STATS[eng]["wins"] += 1

    engine.trade_history.append({
        "token": token,
        "entry_price": entry,
        "exit_price": price,
        "pnl_pct": pnl_pct,
        "engine": eng,
        "alpha": ensure_float(p.get("alpha", 0.0), 0.0),
        "ts": now(),
    })
    engine.trade_history = engine.trade_history[-300:]

    engine.positions = [
        pos for pos in engine.positions
        if isinstance(pos, dict) and pos.get("token") != token
    ]

    engine.stats["sells"] += 1
    engine.last_trade = f"SELL {token[:8]}"
    log(f"SELL {token[:8]} pnl%={pnl_pct*100:+.2f}")

# ================= MONITOR =================
async def monitor_positions():
    while True:
        try:
            repair_positions()

            for p in list(engine.positions):
                if not isinstance(p, dict):
                    continue

                token = p.get("token")
                if not valid_mint(token):
                    continue

                price = await get_price(token)
                if not price:
                    continue

                entry = ensure_float(p.get("entry_price", 0.0), 0.0)
                pnl_pct = (price - entry) / entry if entry > 0 else 0.0

                p["last_price"] = price
                p["pnl_pct"] = pnl_pct
                p["peak_price"] = max(ensure_float(p.get("peak_price", price), price), price)

                if pnl_pct >= 0.18 or pnl_pct <= -0.09:
                    await sell(p)

        except Exception as e:
            repair_engine_state()
            engine.stats["errors"] += 1
            log(f"MONITOR_ERR {str(e)[:120]}")
            log(traceback.format_exc()[:500])

        await asyncio.sleep(6)

# ================= MAIN LOOP =================
async def main():
    log("🚀 v1302_HARDENED_REAL_MARKET_BOT 已啟動 (PAPER MODE)")

    asyncio.create_task(pump_scanner())
    asyncio.create_task(mempool_runner())
    asyncio.create_task(monitor_positions())

    while True:
        try:
            repair_engine_state()
            repair_positions()
            update_allocator()
            await refresh_token_universe()

            candidate_batch = safe_slice(list(CANDIDATES), 10)

            for mint in candidate_batch:
                try:
                    if not valid_mint(mint):
                        continue

                    engine.stats["signals"] += 1

                    liq_ok = await liquidity_ok(mint)
                    if not liq_ok:
                        continue

                    rug_ok = await anti_rug(mint)
                    if not rug_ok:
                        continue

                    alpha = await alpha_engine(mint)
                    if alpha > 0.012:
                        await buy(mint, alpha)

                except Exception as inner_e:
                    repair_engine_state()
                    engine.stats["errors"] += 1
                    log(f"TOKEN_ERR {mint[:8] if isinstance(mint, str) else 'UNKNOWN'} {str(inner_e)[:100]}")

            await asyncio.sleep(7)

        except Exception as e:
            repair_engine_state()
            engine.bot_ok = False
            engine.bot_error = str(e)
            engine.stats["errors"] += 1
            log(f"MAIN_LOOP_ERR {str(e)[:120]}")
            log(traceback.format_exc()[:500])
            await asyncio.sleep(10)

# ================= FastAPI 入口 =================
async def bot_loop():
    while True:
        try:
            engine.bot_ok = True
            engine.bot_error = ""
            await main()
        except Exception as e:
            repair_engine_state()
            engine.bot_ok = False
            engine.bot_error = str(e)
            engine.stats["errors"] += 1
            log(f"bot_loop handled crash: {str(e)[:120]}")
            log(traceback.format_exc()[:500])
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
