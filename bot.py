# ================= v1304_WALLET_TRACKING_ALPHA_BOT =================
import asyncio
import time
import random
from collections import defaultdict

import httpx

from state import engine
from mempool import mempool_stream
from wallet_tracker import (
    wallet_tracker_loop,
    wallet_score,
    discover_active_wallets_from_candidates,
)

# ================= CONFIG =================
SOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wGk3Q3k5Jp3x"
USDT = "Es9vMFrzaCERm7w7z7y7v4JgJ6pG6fQ5gYdExgkt1Py"
BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6YaB1pPB263kzwc"
JUP = "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"

MAX_POSITION_SOL = 0.0025
MIN_POSITION_SOL = 0.001
MAX_POSITIONS = 5

PUMP_API = "https://frontend-api.pump.fun/coins/latest"
JUP_TOKENS_API = "https://token.jup.ag/all"
RPC_URL = "https://api.mainnet-beta.solana.com"

STATIC_UNIVERSE = {SOL, USDC, USDT, BONK, JUP}
FALLBACK_TOKENS = set(STATIC_UNIVERSE)

# 可手動加入公開 wallet，加快效果；留空也能靠自動發現
SMART_WALLETS = []
AUTO_DISCOVER_WALLETS = True
MAX_SMART_WALLETS = 20

HTTP = httpx.AsyncClient(timeout=10.0, follow_redirects=True)

# ================= STATE =================
CANDIDATES = set()
TOKEN_COOLDOWN = defaultdict(float)

PRICE_CACHE = {}
LAST_UNIVERSE_REFRESH = 0
LAST_WALLET_DISCOVERY = 0

PUMP_FAILS = 0
JUP_FAILS = 0
MEMPOOL_FAILS = 0

LAST_LOG_TS = {}
DISCOVERED_WALLETS = set()

# ================= UTIL =================
def now():
    return time.time()

def valid_mint(m):
    return isinstance(m, str) and 32 <= len(m) <= 44

def valid_wallet(w):
    return isinstance(w, str) and 32 <= len(w) <= 44

def ensure_list(x):
    return x if isinstance(x, list) else []

def ensure_dict(x):
    return x if isinstance(x, dict) else {}

def ensure_float(x, d=0.0):
    try:
        return float(x)
    except Exception:
        return d

def ensure_int(x, d=0):
    try:
        return int(x)
    except Exception:
        return d

def safe_slice(x, n):
    return list(x[:n]) if isinstance(x, (list, tuple)) else []

def repair():
    engine.positions = ensure_list(getattr(engine, "positions", []))
    engine.logs = ensure_list(getattr(engine, "logs", []))
    engine.trade_history = ensure_list(getattr(engine, "trade_history", []))

    s = ensure_dict(getattr(engine, "stats", {}))
    engine.stats = {
        "signals": ensure_int(s.get("signals")),
        "buys": ensure_int(s.get("buys")),
        "sells": ensure_int(s.get("sells")),
        "errors": ensure_int(s.get("errors")),
        "adds": ensure_int(s.get("adds")),
    }

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

    if not hasattr(engine, "candidate_count"):
        engine.candidate_count = 0

    if not hasattr(engine, "last_trade"):
        engine.last_trade = ""

    if not hasattr(engine, "last_signal"):
        engine.last_signal = ""

    if not hasattr(engine, "bot_ok"):
        engine.bot_ok = True

    if not hasattr(engine, "bot_error"):
        engine.bot_error = ""

def log(msg):
    repair()
    engine.logs.append(str(msg))
    engine.logs = engine.logs[-300:]
    print("[BOT]", msg)

def log_once(key, msg, cooldown=60):
    t = now()
    if t - LAST_LOG_TS.get(key, 0) > cooldown:
        LAST_LOG_TS[key] = t
        log(msg)

# ================= HTTP =================
async def http_get_json(url, params=None):
    try:
        r = await HTTP.get(url, params=params)
        if r.status_code != 200:
            return None, r.status_code
        return r.json(), 200
    except Exception:
        return None, None

# ================= MARKET =================
async def get_price(mint):
    if not valid_mint(mint):
        return None

    cached = PRICE_CACHE.get(mint)
    if cached and now() - cached[1] < 4:
        return cached[0]

    data, status = await http_get_json(
        "https://lite-api.jup.ag/swap/v1/quote",
        {"inputMint": mint, "outputMint": SOL, "amount": "1000000"},
    )

    if status != 200 or not isinstance(data, dict):
        return None

    out_amount = ensure_int(data.get("outAmount"))
    price = (out_amount / 1e9) / 1_000_000 if out_amount > 0 else None

    PRICE_CACHE[mint] = (price, now())
    return price

async def liquidity_ok(mint):
    data, _ = await http_get_json(
        "https://lite-api.jup.ag/swap/v1/quote",
        {"inputMint": SOL, "outputMint": mint, "amount": "10000000"},
    )
    if not isinstance(data, dict):
        return False

    out_amount = ensure_int(data.get("outAmount"))
    impact = ensure_float(data.get("priceImpactPct"), 1.0)
    return out_amount > 5000 and impact < 0.60

async def anti_rug(mint):
    data, _ = await http_get_json(
        "https://lite-api.jup.ag/swap/v1/quote",
        {"inputMint": mint, "outputMint": SOL, "amount": "1000000"},
    )
    return isinstance(data, dict) and ensure_int(data.get("outAmount")) > 0

# ================= TOKEN SOURCES =================
async def add_candidate(mint, source="unknown"):
    repair()

    if not valid_mint(mint):
        return False

    if mint in CANDIDATES:
        return False

    CANDIDATES.add(mint)
    engine.stats["adds"] += 1
    log_once(f"add_{mint}", f"ADD {mint[:6]} src={source}", cooldown=120)
    return True

async def inject_fallback(reason="fallback"):
    added = 0
    for m in FALLBACK_TOKENS:
        ok = await add_candidate(m, source=reason)
        if ok:
            added += 1
    if added > 0:
        log(f"FALLBACK_OK +{added} reason={reason}")

async def pump_scanner():
    global PUMP_FAILS

    while True:
        data, status = await http_get_json(PUMP_API)

        if status != 200 or not isinstance(data, list):
            PUMP_FAILS += 1
            log_once("pump", f"PUMP_HTTP_{status}", cooldown=60)
            await inject_fallback(reason=f"pump_{status}")
            await asyncio.sleep(min(10 * PUMP_FAILS, 120))
            continue

        PUMP_FAILS = 0

        added = 0
        for c in safe_slice(data, 20):
            if isinstance(c, dict):
                if await add_candidate(c.get("mint"), source="pump"):
                    added += 1

        if added > 0:
            log(f"PUMP_OK +{added}")

        await asyncio.sleep(10)

async def jup_scanner():
    global JUP_FAILS

    while True:
        data, status = await http_get_json(JUP_TOKENS_API)

        if status != 200 or not isinstance(data, list):
            JUP_FAILS += 1
            log_once("jup", f"JUP_ERR {status}", cooldown=90)
            await asyncio.sleep(min(30 * JUP_FAILS, 300))
            continue

        JUP_FAILS = 0
        random.shuffle(data)

        added = 0
        for t in safe_slice(data, 50):
            if isinstance(t, dict):
                mint = t.get("address") or t.get("mint")
                if await add_candidate(mint, source="jup"):
                    added += 1

        if added > 0:
            log(f"JUP_OK +{added}")

        await asyncio.sleep(180)

async def handle_mempool_event(event):
    if not isinstance(event, dict):
        return
    mint = event.get("mint")
    await add_candidate(mint, source="mempool")

async def mempool_runner():
    global MEMPOOL_FAILS

    while True:
        try:
            await mempool_stream(handle_mempool_event)
            MEMPOOL_FAILS = 0
        except Exception as e:
            MEMPOOL_FAILS += 1
            msg = str(e)

            if "429" in msg:
                log_once("mp429", "MEMPOOL_429_BLOCK", cooldown=120)
                await asyncio.sleep(min(60 * MEMPOOL_FAILS, 600))
            else:
                log_once("mp", f"MEMPOOL_ERR {msg[:100]}", cooldown=60)
                await asyncio.sleep(min(5 * MEMPOOL_FAILS, 120))

# ================= WALLET TRACKING =================
async def handle_wallet_token(mint, source="wallet"):
    await add_candidate(mint, source=source)

async def refresh_wallet_discovery():
    global LAST_WALLET_DISCOVERY

    if not AUTO_DISCOVER_WALLETS:
        return

    if now() - LAST_WALLET_DISCOVERY < 900:
        return

    LAST_WALLET_DISCOVERY = now()

    try:
        discovered = await discover_active_wallets_from_candidates(RPC_URL, list(CANDIDATES))
        added = 0

        for row in discovered[:10]:
            wallet = row.get("wallet")
            if not valid_wallet(wallet):
                continue
            if wallet in DISCOVERED_WALLETS:
                continue
            if wallet in SMART_WALLETS:
                continue
            if len(SMART_WALLETS) >= MAX_SMART_WALLETS:
                break

            DISCOVERED_WALLETS.add(wallet)
            SMART_WALLETS.append(wallet)
            added += 1

        if added > 0:
            log(f"WALLET_DISCOVERY_OK +{added}")

    except Exception as e:
        log_once(
            "wallet_discovery_err",
            f"WALLET_DISCOVERY_ERR {str(e)[:100]}",
            cooldown=180,
        )

async def wallet_tracker_bootstrap():
    """
    SMART_WALLETS 一開始可能是空的；
    auto discovery 補進來後，這裡會自動把 wallet tracker 接上。
    """
    started_wallet_sets = set()

    while True:
        try:
            current = [w for w in SMART_WALLETS if valid_wallet(w)]
            wallet_key = tuple(sorted(set(current)))

            if current and wallet_key not in started_wallet_sets:
                started_wallet_sets.add(wallet_key)
                log(f"WALLET_TRACKER_START wallets={len(current)}")
                asyncio.create_task(wallet_tracker_loop(RPC_URL, current, handle_wallet_token))
        except Exception as e:
            log_once(
                "wallet_bootstrap_err",
                f"WALLET_BOOTSTRAP_ERR {str(e)[:100]}",
                cooldown=180,
            )

        await asyncio.sleep(30)

async def refresh_universe():
    global LAST_UNIVERSE_REFRESH

    if now() - LAST_UNIVERSE_REFRESH < 60:
        return

    LAST_UNIVERSE_REFRESH = now()

    if len(CANDIDATES) < 5:
        await inject_fallback(reason="low_universe")

    engine.candidate_count = len(CANDIDATES)
    log_once("universe", f"UNIVERSE_REFRESH total={len(CANDIDATES)}", cooldown=45)

# ================= STRATEGY =================
async def alpha(mint):
    p1 = await get_price(mint)
    await asyncio.sleep(1.2)
    p2 = await get_price(mint)

    if not p1 or not p2 or p1 <= 0:
        return 0.0

    return max(0.0, min(((p2 - p1) / p1) * 1.2, 0.08))

def can_buy(mint):
    repair()

    if mint in {SOL, USDC, USDT}:
        return False

    if len(engine.positions) >= MAX_POSITIONS:
        return False

    if any(isinstance(p, dict) and p.get("token") == mint for p in engine.positions):
        return False

    if now() - TOKEN_COOLDOWN[mint] < 30:
        return False

    return True

async def buy(mint, signal_score):
    repair()

    if not can_buy(mint):
        return False

    price = await get_price(mint)
    if not price or price <= 0:
        return False

    size = MAX_POSITION_SOL * max(0.2, signal_score * 8)
    size = max(MIN_POSITION_SOL, min(size, MAX_POSITION_SOL))
    amount = size / price if price > 0 else 0.0

    engine.positions.append({
        "token": mint,
        "entry_price": price,
        "last_price": price,
        "peak_price": price,
        "pnl_pct": 0.0,
        "amount": amount,
        "engine": "degen",
        "alpha": signal_score,
    })

    TOKEN_COOLDOWN[mint] = now()
    engine.stats["buys"] += 1
    engine.last_trade = f"BUY {mint[:6]}"
    engine.last_signal = f"BUY_SIGNAL {signal_score:.4f}"

    log(f"BUY {mint[:6]} signal={signal_score:.4f} size={size:.6f}")
    return True

async def sell(p):
    repair()

    token = p.get("token")
    if not valid_mint(token):
        return False

    price = await get_price(token)
    if not price:
        return False

    entry = ensure_float(p.get("entry_price"), 0.0)
    if entry <= 0:
        return False

    pnl = (price - entry) / entry

    try:
        engine.positions.remove(p)
    except ValueError:
        engine.positions = [
            x for x in engine.positions
            if not (isinstance(x, dict) and x.get("token") == token)
        ]

    engine.trade_history.append({
        "side": "SELL",
        "token": token,
        "entry_price": entry,
        "exit_price": price,
        "pnl_pct": pnl,
        "alpha": ensure_float(p.get("alpha"), 0.0),
        "engine": p.get("engine", "degen"),
        "ts": now(),
    })
    engine.trade_history = engine.trade_history[-300:]

    engine.stats["sells"] += 1
    engine.last_trade = f"SELL {token[:6]}"

    log(f"SELL {token[:6]} pnl={pnl:.4f}")
    return True

async def monitor():
    while True:
        try:
            repair()

            for p in list(engine.positions):
                if not isinstance(p, dict):
                    continue

                price = await get_price(p.get("token"))
                if not price:
                    continue

                entry = ensure_float(p.get("entry_price"), 0.0)
                if entry <= 0:
                    continue

                pnl = (price - entry) / entry

                p["last_price"] = price
                p["peak_price"] = max(ensure_float(p.get("peak_price"), price), price)
                p["pnl_pct"] = pnl

                if pnl > 0.18 or pnl < -0.10:
                    await sell(p)

        except Exception as e:
            engine.stats["errors"] += 1
            log_once("monitor_err", f"MONITOR_ERR {str(e)[:100]}", cooldown=60)

        await asyncio.sleep(6)

# ================= MAIN =================
async def main():
    log("🚀 v1304 WALLET TRACKING ALPHA BOT START")
    await inject_fallback(reason="boot")

    asyncio.create_task(pump_scanner())
    asyncio.create_task(jup_scanner())
    asyncio.create_task(mempool_runner())
    asyncio.create_task(monitor())
    asyncio.create_task(wallet_tracker_bootstrap())

    while True:
        try:
            repair()
            await refresh_universe()
            await refresh_wallet_discovery()

            candidates = list(CANDIDATES)
            random.shuffle(candidates)

            for mint in safe_slice(candidates, 15):
                engine.stats["signals"] += 1

                liq_ok = await liquidity_ok(mint)
                if not liq_ok:
                    log_once(f"liq_{mint}", f"SKIP_LIQ {mint[:6]}", cooldown=120)
                    continue

                rug_ok = await anti_rug(mint)
                if not rug_ok:
                    log_once(f"rug_{mint}", f"SKIP_RUG {mint[:6]}", cooldown=120)
                    continue

                a = await alpha(mint)
                w_score = wallet_score(mint)
                combo = a + min(w_score * 0.01, 0.05)

                engine.last_signal = f"{mint[:6]} a={a:.4f} w={w_score:.2f} c={combo:.4f}"

                log_once(
                    f"combo_{mint}",
                    f"SIGNAL {mint[:6]} alpha={a:.4f} wallet={w_score:.2f} combo={combo:.4f}",
                    cooldown=90,
                )

                if combo > 0.006:
                    await buy(mint, combo)

            engine.engine_stats = {
                "stable": {"pnl": 0.0, "trades": 0, "wins": 0},
                "degen": {"pnl": 0.0, "trades": len(engine.trade_history), "wins": 0},
                "sniper": {"pnl": 0.0, "trades": 0, "wins": 0},
            }
            engine.engine_allocator = {
                "stable": 0.4,
                "degen": 0.4,
                "sniper": 0.2,
            }

            await asyncio.sleep(6)

        except Exception as e:
            repair()
            engine.stats["errors"] += 1
            engine.bot_ok = False
            engine.bot_error = str(e)
            log(f"MAIN_ERR {str(e)[:80]}")
            await asyncio.sleep(5)

# ================= ENTRY =================
async def bot_loop():
    try:
        engine.bot_ok = True
        engine.bot_error = ""
        await main()
    except asyncio.CancelledError:
        log("BOT_STOPPED")
        raise
    except Exception as e:
        repair()
        engine.bot_ok = False
        engine.bot_error = str(e)
        log(f"FATAL {e}")
        raise
