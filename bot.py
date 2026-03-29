# ================= v1303_LIVE_UNIVERSE_BOT =================
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
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wGk3Q3k5Jp3x"
USDT = "Es9vMFrzaCERm7w7z7y7v4JgJ6pG6fQ5gYdExgkt1Py"

MAX_POSITION_SOL = 0.0025
MIN_POSITION_SOL = 0.001
MAX_POSITIONS = 5

PUMP_API = "https://frontend-api.pump.fun/coins/latest"
JUP_TOKENS_API = "https://token.jup.ag/all"

SEED_TOKENS = {SOL}
FALLBACK_TOKENS = {
    SOL,
    USDC,
    USDT,
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6YaB1pPB263kzwc",   # BONK
    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",   # JUP
}

HTTP = httpx.AsyncClient(timeout=10.0, follow_redirects=True)

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

def now():
    return time.time()

def valid_mint(m):
    return isinstance(m, str) and 32 <= len(m) <= 44

def repair_engine_state():
    engine.positions = ensure_list(getattr(engine, "positions", []))
    engine.trade_history = ensure_list(getattr(engine, "trade_history", []))

    logs = getattr(engine, "logs", [])
    try:
        engine.logs = list(logs)[-300:]
    except Exception:
        engine.logs = []

    raw_stats = ensure_dict(getattr(engine, "stats", {}))
    engine.stats = {
        "signals": ensure_int(raw_stats.get("signals", 0)),
        "buys": ensure_int(raw_stats.get("buys", 0)),
        "sells": ensure_int(raw_stats.get("sells", 0)),
        "errors": ensure_int(raw_stats.get("errors", 0)),
        "adds": ensure_int(raw_stats.get("adds", 0)),
    }

    engine.running = bool(getattr(engine, "running", True))
    engine.mode = str(getattr(engine, "mode", "PAPER"))
    engine.capital = ensure_float(getattr(engine, "capital", 1.0), 1.0)
    engine.sol_balance = ensure_float(getattr(engine, "sol_balance", 1.0), 1.0)
    engine.last_trade = str(getattr(engine, "last_trade", ""))
    engine.last_signal = str(getattr(engine, "last_signal", ""))
    engine.bot_ok = bool(getattr(engine, "bot_ok", True))
    engine.bot_error = str(getattr(engine, "bot_error", ""))
    engine.candidate_count = ensure_int(getattr(engine, "candidate_count", 0), 0)

    if not hasattr(engine, "engine_stats") or not isinstance(engine.engine_stats, dict):
        engine.engine_stats = {
            "stable": {"pnl": 0.0, "trades": 0, "wins": 0},
            "degen": {"pnl": 0.0, "trades": 0, "wins": 0},
            "sniper": {"pnl": 0.0, "trades": 0, "wins": 0},
        }

    if not hasattr(engine, "engine_allocator") or not isinstance(engine.engine_allocator, dict):
        engine.engine_allocator = {
            "stable": 0.4,
            "degen": 0.4,
            "sniper": 0.2,
        }

def log(msg):
    repair_engine_state()
    engine.logs.append(str(msg))
    engine.logs = engine.logs[-300:]
    print(f"[BOT] {msg}")

def normalize_position(p):
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
PRICE_CACHE = {}
ALPHA_CACHE = {}

LAST_UNIVERSE_REFRESH = 0.0
LAST_JUP_REFRESH = 0.0
LAST_PUMP_REFRESH = 0.0

# ================= HTTP =================
async def http_get_json(url, params=None):
    try:
        r = await HTTP.get(url, params=params)
        if r.status_code != 200:
            return None, r.status_code
        try:
            return r.json(), 200
        except Exception:
            return None, 200
    except Exception:
        return None, None

# ================= QUOTES =================
async def get_quote(input_mint, output_mint, amount):
    data, status = await http_get_json(
        "https://lite-api.jup.ag/swap/v1/quote",
        params={
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount),
        },
    )
    if status != 200 or not isinstance(data, dict):
        return None
    return data

async def get_price(mint):
    if not valid_mint(mint):
        return None

    cached = PRICE_CACHE.get(mint)
    if isinstance(cached, tuple) and len(cached) == 2 and now() - cached[1] < 4:
        return cached[0]

    quote = await get_quote(mint, SOL, 1_000_000)
    if not quote:
        return None

    out_amount = ensure_int(quote.get("outAmount", 0), 0)
    price = (out_amount / 1e9) / 1_000_000 if out_amount > 0 else None
    PRICE_CACHE[mint] = (price, now())
    return price

async def get_liquidity_and_impact(mint):
    if not valid_mint(mint):
        return 0, 1.0

    quote = await get_quote(SOL, mint, 10_000_000)
    if not quote:
        return 0, 1.0

    out_amount = ensure_int(quote.get("outAmount", 0), 0)
    impact = ensure_float(quote.get("priceImpactPct", 1.0), 1.0)
    return out_amount, impact

# ================= FILTERS =================
async def liquidity_ok(mint):
    out_amount, impact = await get_liquidity_and_impact(mint)
    return out_amount > 5000 and impact < 0.40

async def anti_rug(mint):
    quote = await get_quote(mint, SOL, 1_000_000)
    if not quote:
        return False
    return ensure_int(quote.get("outAmount", 0), 0) > 0

# ================= TOKEN SOURCES =================
async def add_candidate(mint, source="unknown"):
    if not valid_mint(mint):
        return False
    if mint in CANDIDATES:
        return False
    CANDIDATES.add(mint)
    repair_engine_state()
    engine.stats["adds"] += 1
    return True

async def inject_fallback_tokens(reason="fallback"):
    added = 0
    for mint in FALLBACK_TOKENS:
        ok = await add_candidate(mint, reason)
        if ok:
            added += 1
    if added > 0:
        log(f"FALLBACK_OK +{added} reason={reason}")

async def pump_scanner():
    global LAST_PUMP_REFRESH
    while True:
        try:
            data, status = await http_get_json(PUMP_API)

            if status != 200:
                log(f"PUMP_HTTP_{status}")
                await inject_fallback_tokens(f"pump_http_{status}")
                await asyncio.sleep(8)
                continue

            if not isinstance(data, list):
                log("PUMP_BAD_PAYLOAD")
                await inject_fallback_tokens("pump_bad_payload")
                await asyncio.sleep(8)
                continue

            added = 0
            for row in safe_slice(data, 20):
                if not isinstance(row, dict):
                    continue
                mint = row.get("mint")
                if await add_candidate(mint, "pump"):
                    added += 1

            LAST_PUMP_REFRESH = now()
            if added > 0:
                log(f"PUMP_OK +{added}")

        except Exception as e:
            repair_engine_state()
            engine.stats["errors"] += 1
            log(f"PUMP_ERR {str(e)[:100]}")
            await inject_fallback_tokens("pump_exception")

        await asyncio.sleep(8)

async def jup_token_scanner():
    global LAST_JUP_REFRESH
    while True:
        try:
            data, status = await http_get_json(JUP_TOKENS_API)

            if status != 200 or not isinstance(data, list):
                log(f"JUP_TOKENLIST_ERR {status}")
                await asyncio.sleep(60)
                continue

            random.shuffle(data)
            added = 0

            for row in safe_slice(data, 60):
                if not isinstance(row, dict):
                    continue
                mint = row.get("address") or row.get("mint")
                if await add_candidate(mint, "jup"):
                    added += 1

            LAST_JUP_REFRESH = now()
            if added > 0:
                log(f"JUP_TOKENLIST_OK +{added}")

        except Exception as e:
            repair_engine_state()
            engine.stats["errors"] += 1
            log(f"JUP_TOKENLIST_EX {str(e)[:100]}")

        await asyncio.sleep(180)

async def handle_mempool(event):
    try:
        if not isinstance(event, dict):
            return
        mint = event.get("mint")
        if await add_candidate(mint, "mempool"):
            log(f"MEMPOOL_ADD {mint[:8]}")
    except Exception as e:
        repair_engine_state()
        engine.stats["errors"] += 1
        log(f"MEMPOOL_HANDLE_ERR {str(e)[:100]}")

async def mempool_runner():
    while True:
        try:
            await mempool_stream(handle_mempool)
        except Exception as e:
            repair_engine_state()
            engine.stats["errors"] += 1
            log(f"MEMPOOL_STREAM_ERR {str(e)[:120]}")
            await asyncio.sleep(3)

async def refresh_token_universe():
    global LAST_UNIVERSE_REFRESH
    if now() - LAST_UNIVERSE_REFRESH < 90:
        return

    LAST_UNIVERSE_REFRESH = now()

    CANDIDATES.update(SEED_TOKENS)

    if len(CANDIDATES) < 5:
        await inject_fallback_tokens("low_universe")

    engine.candidate_count = len(CANDIDATES)
    log(f"UNIVERSE_REFRESH total={len(CANDIDATES)}")

# ================= ALPHA =================
async def alpha_engine(mint):
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

def pick_engine(alpha):
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
def can_buy(mint):
    repair_positions()

    if not valid_mint(mint):
        return False
    if mint in {SOL, USDC, USDT}:
        return False
    if len(engine.positions) >= MAX_POSITIONS:
        return False
    if any(isinstance(p, dict) and p.get("token") == mint for p in engine.positions):
        return False
    if now() - TOKEN_COOLDOWN[mint] < 20:
        return False
    return True

async def buy(mint, alpha):
    repair_positions()

    if not can_buy(mint):
        return False

    eng = pick_engine(alpha)
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

async def sell(p):
    repair_positions()

    token = p.get("token")
    if not valid_mint(token):
        return

    price = await get_price(token)
    if not price:
        return

    entry = ensure_float(p.get("entry_price", price), price)
    pnl_pct = (price - entry) / entry if entry > 0 else 0.0
    eng = str(p.get("engine", "stable"))
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
        "side": "SELL",
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
    log("🚀 v1303_LIVE_UNIVERSE_BOT 已啟動 (PAPER MODE)")

    await inject_fallback_tokens("boot")

    asyncio.create_task(pump_scanner())
    asyncio.create_task(jup_token_scanner())
    asyncio.create_task(mempool_runner())
    asyncio.create_task(monitor_positions())

    while True:
        try:
            repair_engine_state()
            repair_positions()
            update_allocator()
            await refresh_token_universe()

            candidates = list(CANDIDATES)
            random.shuffle(candidates)
            candidate_batch = safe_slice(candidates, 15)

            for mint in candidate_batch:
                try:
                    if not valid_mint(mint):
                        continue

                    engine.stats["signals"] += 1

                    if not await liquidity_ok(mint):
                        continue
                    if not await anti_rug(mint):
                        continue

                    alpha = await alpha_engine(mint)
                    if alpha > 0.012:
                        await buy(mint, alpha)

                except Exception as inner_e:
                    repair_engine_state()
                    engine.stats["errors"] += 1
                    short = mint[:8] if isinstance(mint, str) else "UNKNOWN"
                    log(f"TOKEN_ERR {short} {str(inner_e)[:100]}")

            await asyncio.sleep(7)

        except Exception as e:
            repair_engine_state()
            engine.bot_ok = False
            engine.bot_error = str(e)
            engine.stats["errors"] += 1
            log(f"MAIN_LOOP_ERR {str(e)[:120]}")
            log(traceback.format_exc()[:500])
            await asyncio.sleep(8)

# ================= FASTAPI ENTRY =================
async def bot_loop():
    try:
        await main()
    except asyncio.CancelledError:
        log("BOT_STOPPED")
        raise
    except Exception as e:
        repair_engine_state()
        engine.bot_ok = False
        engine.bot_error = str(e)
        engine.stats["errors"] += 1
        log(f"BOT_FATAL {str(e)[:120]}")
        log(traceback.format_exc()[:500])
        raise

if __name__ == "__main__":
    asyncio.run(main())
