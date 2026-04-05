# ================= V50 FINAL FUSION FUND ENGINE =================

import os
import asyncio
import time
import random
import json
from collections import defaultdict, Counter

import httpx
import websockets

from app.state import engine
from app.alpha.adaptive_filter import adaptive_filter

try:
    from app.execution.jupiter_exec import execute_swap
except Exception:
    async def execute_swap(a, b, c):
        return {"paper": True, "quote": {"outAmount": "0"}}

try:
    from app.data.market import get_quote
except Exception:
    async def get_quote(a, b, c):
        return None

try:
    from app.alpha.helius_wallet_tracker import update_token_wallets
except Exception:
    async def update_token_wallets(m):
        return []


# ================= CONFIG =================

REAL_TRADING = os.getenv("REAL_TRADING", "false").lower() == "true"

SOL = "So11111111111111111111111111111111111111112"
SOL_DECIMALS = 1_000_000_000
AMOUNT = int(os.getenv("AMOUNT", "1000000"))

MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "2"))
MAX_EXPOSURE = float(os.getenv("MAX_EXPOSURE", "0.35"))
MAX_POSITION_SIZE = float(os.getenv("MAX_POSITION_SIZE", "0.03"))

TAKE_PROFIT = float(os.getenv("TAKE_PROFIT", "0.02"))
STOP_LOSS = float(os.getenv("STOP_LOSS", "-0.03"))
TRAILING_GAP = float(os.getenv("TRAILING_GAP", "0.01"))
MAX_HOLD_SEC = int(os.getenv("MAX_HOLD_SEC", "120"))

HARD_STOP_LOSS = float(os.getenv("HARD_STOP_LOSS", "-0.04"))
FORCE_EXIT_SEC = int(os.getenv("FORCE_EXIT_SEC", "90"))

TOKEN_COOLDOWN = int(os.getenv("TOKEN_COOLDOWN", "15"))
BLACKLIST_TIME = int(os.getenv("BLACKLIST_TIME", "60"))
FORCE_TRADE_AFTER = int(os.getenv("FORCE_TRADE_AFTER", "20"))
LOOP_SLEEP_SEC = float(os.getenv("LOOP_SLEEP_SEC", "2"))

ENTRY_THRESHOLD = float(os.getenv("ENTRY_THRESHOLD", "0.12"))
FILTER_SCORE_BYPASS = float(os.getenv("FILTER_SCORE_BYPASS", "0.12"))
SOFT_DISABLE_FILTER = os.getenv("SOFT_DISABLE_FILTER", "false").lower() == "true"

MIN_ORDER_SOL = float(os.getenv("MIN_ORDER_SOL", "0.01"))

MIN_PRICE = float(os.getenv("MIN_PRICE", "0.0000000001"))
MAX_PRICE_JUPITER = float(os.getenv("MAX_PRICE_JUPITER", "0.1"))
MAX_PRICE_FALLBACK = float(os.getenv("MAX_PRICE_FALLBACK", "10"))
MIN_LIQUIDITY_TRADE = float(os.getenv("MIN_LIQUIDITY_TRADE", "50000"))
MIN_LIQUIDITY_OBSERVE = float(os.getenv("MIN_LIQUIDITY_OBSERVE", "5000"))

MAX_BREAKOUT_ABS = float(os.getenv("MAX_BREAKOUT_ABS", "0.20"))
MAX_SCORE = float(os.getenv("MAX_SCORE", "1.5"))
MAX_PNL_ABS = float(os.getenv("MAX_PNL_ABS", "0.2"))
MAX_CAPITAL = float(os.getenv("MAX_CAPITAL", "20"))

MIN_OUT_AMOUNT = int(os.getenv("MIN_OUT_AMOUNT", "300"))
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "6"))
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "").strip()

MIN_UNIVERSE = int(os.getenv("MIN_UNIVERSE", "20"))
BOOT_SYNTHETIC_UNIVERSE = os.getenv("BOOT_SYNTHETIC_UNIVERSE", "true").lower() == "true"

ADAPTIVE_THRESHOLD_MIN = float(os.getenv("ADAPTIVE_THRESHOLD_MIN", "0.04"))
ADAPTIVE_THRESHOLD_MAX = float(os.getenv("ADAPTIVE_THRESHOLD_MAX", "0.10"))

TOP_N_TO_TRADE = int(os.getenv("TOP_N_TO_TRADE", "1"))
MAX_TOKENS_PER_CYCLE = int(os.getenv("MAX_TOKENS_PER_CYCLE", "60"))
TOP_K_PRESELECT = int(os.getenv("TOP_K_PRESELECT", "2"))

MEMPOOL_WSS = os.getenv("MEMPOOL_WSS", "wss://api.mainnet-beta.solana.com")

MIN_CONFIRM_MOMENTUM = float(os.getenv("MIN_CONFIRM_MOMENTUM", "0.006"))
MIN_CONFIRM_BREAKOUT = float(os.getenv("MIN_CONFIRM_BREAKOUT", "0.008"))
STRICT_A_TIER_THRESHOLD = float(os.getenv("STRICT_A_TIER_THRESHOLD", "0.12"))

BREATHING_LOSS_STREAK = int(os.getenv("BREATHING_LOSS_STREAK", "2"))
BREATHING_COOLDOWN_SEC = int(os.getenv("BREATHING_COOLDOWN_SEC", "180"))
BREATHING_MIN_RISK_MULT = float(os.getenv("BREATHING_MIN_RISK_MULT", "0.45"))
BREATHING_MAX_RISK_MULT = float(os.getenv("BREATHING_MAX_RISK_MULT", "1.20"))

# execution quality guard
MAX_NEW_BUYS_PER_CYCLE = int(os.getenv("MAX_NEW_BUYS_PER_CYCLE", "1"))
MAX_BUYS_PER_10MIN = int(os.getenv("MAX_BUYS_PER_10MIN", "12"))
BUY_WINDOW_SEC = int(os.getenv("BUY_WINDOW_SEC", "600"))

# alpha weights
ALPHA_BREAKOUT_WEIGHT = float(os.getenv("ALPHA_BREAKOUT_WEIGHT", "0.35"))
ALPHA_MOMENTUM_WEIGHT = float(os.getenv("ALPHA_MOMENTUM_WEIGHT", "0.25"))
ALPHA_SMART_WEIGHT = float(os.getenv("ALPHA_SMART_WEIGHT", "0.25"))
ALPHA_LIQ_WEIGHT = float(os.getenv("ALPHA_LIQ_WEIGHT", "0.10"))
ALPHA_WALLET_WEIGHT = float(os.getenv("ALPHA_WALLET_WEIGHT", "0.05"))

SNIPER_MULTIPLIER = float(os.getenv("SNIPER_MULTIPLIER", "1.30"))
SMART_MULTIPLIER = float(os.getenv("SMART_MULTIPLIER", "1.20"))
MOMENTUM_MULTIPLIER = float(os.getenv("MOMENTUM_MULTIPLIER", "1.00"))

SEARCH_TERMS = [
    "SOL", "USDC", "BONK",
    "MEME", "PEPE", "DOG", "AI",
    "PUMP", "NEW", "MOON", "100x"
]


# ================= RUNTIME MEMORY =================

LAST_TRADE = defaultdict(float)
LAST_PRICE = {}
LAST_MOMENTUM = {}
LAST_PRICE_SOURCE = {}

TOKEN_TRADE_COUNT = defaultdict(int)
BLACKLIST = {}

SOURCE_STATS = defaultdict(lambda: {
    "count": 0,
    "wins": 0,
    "losses": 0,
    "total_pnl": 0.0,
})

STRATEGY_STATS = defaultdict(lambda: {
    "count": 0,
    "wins": 0,
    "losses": 0,
    "total_pnl": 0.0,
})

SCORE_COMPONENT_STATS = defaultdict(lambda: {
    "count": 0,
    "sum": 0.0,
})

BUY_TIMES = []

MEMPOOL_BUFFER = []
MEMPOOL_TASK = None

BREATHING_STATE = {
    "risk_mult": 1.0,
    "cooldown_until": 0.0,
}

REGIME_STATE = {
    "mode": "neutral",
    "last_update": 0.0,
}


# ================= ENGINE =================

def ensure_engine():
    engine.positions = getattr(engine, "positions", [])
    engine.trade_history = getattr(engine, "trade_history", [])
    engine.logs = getattr(engine, "logs", [])

    engine.capital = float(getattr(engine, "capital", 5.0))
    engine.start_capital = float(getattr(engine, "start_capital", engine.capital))
    engine.peak_capital = float(getattr(engine, "peak_capital", engine.capital))

    engine.running = getattr(engine, "running", True)
    engine.no_trade_cycles = int(getattr(engine, "no_trade_cycles", 0))

    engine.last_signal = getattr(engine, "last_signal", "")
    engine.last_trade = getattr(engine, "last_trade", "")

    engine.stats = getattr(engine, "stats", {})
    engine.stats.setdefault("signals", 0)
    engine.stats.setdefault("executed", 0)
    engine.stats.setdefault("rejected", 0)
    engine.stats.setdefault("errors", 0)
    engine.stats.setdefault("open_positions", 0)
    engine.stats.setdefault("open_exposure", 0.0)
    engine.stats.setdefault("trades", 0)
    engine.stats.setdefault("wins", 0)
    engine.stats.setdefault("losses", 0)
    engine.stats.setdefault("forced_trades", 0)


# ================= LOG =================

def log(x):
    print(x)
    engine.logs.append(str(x))
    engine.logs = engine.logs[-900:]


# ================= HELPERS =================

def sf(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default

def now():
    return time.time()

def clamp(x, lo, hi):
    return max(lo, min(hi, x))

def exposure():
    return sum(sf(p.get("size", 0.0)) for p in engine.positions)

def update_open_stats():
    engine.stats["open_positions"] = len(engine.positions)
    engine.stats["open_exposure"] = exposure()

def update_peak_capital():
    engine.peak_capital = max(sf(engine.peak_capital), sf(engine.capital))

def push_trade(row):
    engine.trade_history.append(row)
    engine.trade_history = engine.trade_history[-1000:]
    engine.stats["trades"] = len(engine.trade_history)

def source_stat_win(src, pnl):
    s = SOURCE_STATS[src]
    s["count"] += 1
    s["wins"] += 1
    s["total_pnl"] += pnl

def source_stat_loss(src, pnl):
    s = SOURCE_STATS[src]
    s["count"] += 1
    s["losses"] += 1
    s["total_pnl"] += pnl

def strategy_stat_update(strategy, pnl):
    s = STRATEGY_STATS[strategy]
    s["count"] += 1
    s["total_pnl"] += pnl
    if pnl > 0:
        s["wins"] += 1
    else:
        s["losses"] += 1

def score_stat_add(name, value):
    s = SCORE_COMPONENT_STATS[name]
    s["count"] += 1
    s["sum"] += sf(value)

def dedup(tokens):
    seen = set()
    out = []
    for t in tokens:
        m = t.get("mint")
        if not m or m in seen:
            continue
        seen.add(m)
        out.append(t)
    return out

def limit_token_frequency(tokens, max_per_token=2):
    count = Counter()
    out = []
    for t in tokens:
        m = t.get("mint")
        if not m:
            continue
        if count[m] >= max_per_token:
            continue
        count[m] += 1
        out.append(t)
    return out

def current_dynamic_threshold():
    base = ENTRY_THRESHOLD

    regime = detect_regime()
    if regime == "bull":
        base *= 0.92
    elif regime == "bear":
        base *= 1.12

    if engine.no_trade_cycles > 30:
        base *= 0.75
    elif engine.no_trade_cycles > 15:
        base *= 0.88

    return clamp(base, ADAPTIVE_THRESHOLD_MIN, ADAPTIVE_THRESHOLD_MAX)

def breakout_strength(b):
    b = clamp(sf(b), -MAX_BREAKOUT_ABS, MAX_BREAKOUT_ABS)
    if b <= 0:
        return 0.0
    return min(b / 0.05, 1.0) * 0.35

def momentum_strength(m):
    m = clamp(sf(m), -MAX_BREAKOUT_ABS, MAX_BREAKOUT_ABS)
    if m <= 0:
        return 0.0
    return min(m / 0.05, 1.0) * 0.30

def recent_closed_trades(n=5):
    hist = getattr(engine, "trade_history", []) or []
    rows = [x for x in hist if isinstance(x, dict)]
    return rows[-n:]

def breathing_risk_mult():
    x = sf(BREATHING_STATE.get("risk_mult", 1.0), 1.0)
    return clamp(x, BREATHING_MIN_RISK_MULT, BREATHING_MAX_RISK_MULT)

def update_breathing_state():
    rows = recent_closed_trades(6)
    if not rows:
        BREATHING_STATE["risk_mult"] = 1.0
        return

    last2 = rows[-2:] if len(rows) >= 2 else rows
    loss_streak = 0
    for r in reversed(last2):
        if sf(r.get("pnl"), 0.0) < 0:
            loss_streak += 1
        else:
            break

    if loss_streak >= BREATHING_LOSS_STREAK:
        BREATHING_STATE["risk_mult"] = max(
            BREATHING_MIN_RISK_MULT,
            BREATHING_STATE["risk_mult"] * 0.70
        )
        BREATHING_STATE["cooldown_until"] = now() + BREATHING_COOLDOWN_SEC
        log(
            f"BREATHING_DE_RISK loss_streak={loss_streak} "
            f"risk_mult={BREATHING_STATE['risk_mult']:.2f}"
        )
        return

    recent = rows[-3:]
    if recent and all(sf(x.get("pnl"), 0.0) > 0 for x in recent):
        BREATHING_STATE["risk_mult"] = min(
            BREATHING_MAX_RISK_MULT,
            BREATHING_STATE["risk_mult"] + 0.08
        )
        log(f"BREATHING_RE_RISK risk_mult={BREATHING_STATE['risk_mult']:.2f}")
        return

    if now() > sf(BREATHING_STATE.get("cooldown_until", 0.0), 0.0):
        BREATHING_STATE["risk_mult"] = min(
            BREATHING_MAX_RISK_MULT,
            BREATHING_STATE["risk_mult"] + 0.03
        )

def detect_regime():
    # cache 15 sec
    if now() - sf(REGIME_STATE.get("last_update", 0.0), 0.0) < 15:
        return REGIME_STATE["mode"]

    rows = recent_closed_trades(8)
    if len(rows) < 4:
        REGIME_STATE["mode"] = "neutral"
        REGIME_STATE["last_update"] = now()
        return "neutral"

    pnls = [sf(x.get("pnl"), 0.0) for x in rows]
    wins = sum(1 for x in pnls if x > 0)
    avg_pnl = sum(pnls) / max(len(pnls), 1)
    winrate = wins / max(len(pnls), 1)

    mode = "neutral"
    if winrate >= 0.60 and avg_pnl > 0:
        mode = "bull"
    elif winrate <= 0.30 and avg_pnl < 0:
        mode = "bear"

    REGIME_STATE["mode"] = mode
    REGIME_STATE["last_update"] = now()
    return mode

def buy_window_count():
    cutoff = now() - BUY_WINDOW_SEC
    while BUY_TIMES and BUY_TIMES[0] < cutoff:
        BUY_TIMES.pop(0)
    return len(BUY_TIMES)


# ================= HTTP =================

async def http_get(url, params=None, headers=None, timeout=HTTP_TIMEOUT):
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(url, params=params, headers=headers)
            r.raise_for_status()
            return r.json()
    except Exception:
        return None


# ================= MEMPOOL + DEX BULK =================

async def mempool_stream():
    while True:
        try:
            async with websockets.connect(MEMPOOL_WSS, ping_interval=20) as ws:
                sub = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "logsSubscribe",
                    "params": [
                        {
                            "mentions": [
                                "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5W6s8nH9c"
                            ]
                        },
                        {"commitment": "processed"}
                    ]
                }
                await ws.send(json.dumps(sub))

                while True:
                    msg = await ws.recv()
                    data = json.loads(msg)
                    text = json.dumps(data)

                    for word in text.replace('"', " ").replace(",", " ").split():
                        if 32 <= len(word) <= 48 and word.isalnum():
                            MEMPOOL_BUFFER.append({
                                "mint": word,
                                "source": "mempool",
                                "meta": {},
                            })
                            if len(MEMPOOL_BUFFER) > 300:
                                del MEMPOOL_BUFFER[:-300]
        except Exception as e:
            log(f"MEMPOOL_ERR {e}")
            await asyncio.sleep(2)

def flush_mempool():
    out = []
    while MEMPOOL_BUFFER:
        out.append(MEMPOOL_BUFFER.pop(0))
    return out

async def fetch_dex_bulk():
    tasks = [fetch_dexscreener_candidates(q) for q in SEARCH_TERMS]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    merged = []
    for r in results:
        if isinstance(r, list):
            merged.extend(r)
    return merged


# ================= TRUE ALPHA SOURCES =================

async def fetch_fusion_candidates():
    try:
        from app.sources.fusion import fetch_candidates
        data = await fetch_candidates()
        if not isinstance(data, list):
            log("FUSION: 0")
            return []
        out = []
        for x in data:
            m = x.get("mint")
            if not m:
                continue
            out.append({
                "mint": m,
                "source": x.get("source", "fusion"),
                "meta": x,
            })
        log(f"FUSION: {len(out)}")
        return out
    except Exception:
        log("FUSION: 0")
        return []

async def fetch_pumpfun_candidates(limit=30):
    url = "https://frontend-api.pump.fun/coins/latest"
    data = await http_get(url)

    out = []
    if not isinstance(data, list):
        log("PUMPFUN_EMPTY")
        return out

    for row in data[:limit]:
        mint = row.get("mint")
        if not mint:
            continue
        out.append({
            "mint": mint,
            "source": "pumpfun",
            "meta": {
                "symbol": row.get("symbol"),
                "name": row.get("name"),
                "created_timestamp": row.get("created_timestamp"),
                "reply_count": row.get("reply_count"),
                "market_cap": row.get("market_cap"),
            }
        })
    return out

async def fetch_jupiter_candidates(limit=80):
    urls = [
        "https://lite-api.jup.ag/tokens/v1/mints/tradable",
        "https://cache.jup.ag/tokens",
    ]

    all_rows = []
    for url in urls:
        data = await http_get(url)
        if isinstance(data, list):
            all_rows.extend(data)

    if not all_rows:
        log("JUPITER_EMPTY")

    out = []
    random.shuffle(all_rows)

    for row in all_rows[:limit]:
        if isinstance(row, str):
            mint = row
            meta = {}
        else:
            mint = row.get("address") or row.get("mint")
            meta = row

        if not mint or mint == SOL:
            continue

        out.append({
            "mint": mint,
            "source": "jupiter",
            "meta": {
                "symbol": meta.get("symbol"),
                "name": meta.get("name"),
                "decimals": meta.get("decimals"),
            }
        })
    return out

async def fetch_dexscreener_candidates(query="SOL", limit=30):
    data = await http_get(
        "https://api.dexscreener.com/latest/dex/search/",
        params={"q": query}
    )

    out = []
    if not data:
        return out

    pairs = data.get("pairs", [])
    if not isinstance(pairs, list):
        return out

    for row in pairs[:limit]:
        base = row.get("baseToken", {}) or {}
        mint = base.get("address")
        if not mint or mint == SOL:
            continue

        out.append({
            "mint": mint,
            "source": "dexscreener",
            "meta": {
                "symbol": base.get("symbol"),
                "name": base.get("name"),
                "liquidity_usd": (row.get("liquidity", {}) or {}).get("usd"),
                "volume_h24": (row.get("volume", {}) or {}).get("h24"),
                "price_usd": row.get("priceUsd"),
                "price_native": row.get("priceNative"),
                "pair_address": row.get("pairAddress"),
            }
        })
    return out

async def fetch_alpha_candidates():
    results = await asyncio.gather(
        fetch_fusion_candidates(),
        fetch_pumpfun_candidates(),
        fetch_jupiter_candidates(),
        fetch_dex_bulk(),
        return_exceptions=True,
    )

    merged = []
    for r in results:
        if isinstance(r, list):
            merged.extend(r)

    merged.extend(flush_mempool())

    seen = set()
    out = []
    for t in merged:
        m = t.get("mint")
        if not m or m in seen:
            continue
        seen.add(m)
        out.append(t)

    if len(out) < MIN_UNIVERSE and BOOT_SYNTHETIC_UNIVERSE:
        log(f"LOW_UNIVERSE_BOOT {len(out)}")
        for i in range(10):
            out.append({
                "mint": f"SIM{i}{random.randint(1000,9999)}",
                "source": "synthetic",
                "meta": {},
            })

    return out

def source_quality(source):
    if source == "pumpfun":
        return 1.18
    if source == "mempool":
        return 1.22
    if source == "dexscreener":
        return 0.75
    if source == "fusion":
        return 1.05
    if source == "jupiter":
        return 1.00
    if source == "synthetic":
        return 0.25
    return 1.00


# ================= PRICE SOURCES =================

async def safe_quote(input_mint, output_mint, amount):
    for _ in range(3):
        try:
            q = await get_quote(input_mint, output_mint, amount)
            if q:
                return q
        except Exception:
            pass
        await asyncio.sleep(0.15)
    return None

async def jupiter_price(m):
    q = await safe_quote(SOL, m, AMOUNT)
    if not q:
        return None

    in_amt = sf(q.get("inAmount", 0))
    out_amt = sf(q.get("outAmount", 0))

    if in_amt <= 0 or out_amt <= 0:
        return None

    if out_amt < MIN_OUT_AMOUNT:
        log(f"LOW_LIQ {m[:6]} {int(out_amt)}")
        return None

    price = in_amt / out_amt

    if price <= 0 or price > MAX_PRICE_JUPITER:
        log(f"BAD_PRICE {m[:6]} {price:.10f}")
        return None

    return {
        "price": price,
        "liq": out_amt,
        "source": "jupiter",
    }

async def birdeye_price(m):
    if not BIRDEYE_API_KEY:
        return None

    headers = {"X-API-KEY": BIRDEYE_API_KEY}
    token_res = await http_get(
        "https://public-api.birdeye.so/defi/price",
        params={"address": m},
        headers=headers,
    )
    sol_res = await http_get(
        "https://public-api.birdeye.so/defi/price",
        params={"address": SOL},
        headers=headers,
    )

    try:
        token_usd = sf(token_res["data"]["value"])
        sol_usd = sf(sol_res["data"]["value"])
        if token_usd <= 0 or sol_usd <= 0:
            return None

        price = token_usd / sol_usd
        if price <= 0 or price > MAX_PRICE_FALLBACK:
            return None

        return {
            "price": price,
            "liq": 0,
            "source": "birdeye",
        }
    except Exception:
        return None

async def dexscreener_price(m):
    res = await http_get(
        "https://api.dexscreener.com/latest/dex/search/",
        params={"q": m}
    )
    if not res:
        return None

    try:
        pairs = res.get("pairs", [])
        if not pairs:
            return None

        pairs = sorted(
            pairs,
            key=lambda x: sf((x.get("liquidity", {}) or {}).get("usd", 0)),
            reverse=True,
        )
        pair = pairs[0]

        native_price = sf(pair.get("priceNative", 0))
        liq = sf((pair.get("liquidity", {}) or {}).get("usd", 0))

        if native_price > 10:
            log(f"DEX_SKIP_BAD_UNIT {m[:6]} {native_price}")
            return None

        if native_price <= 0 or native_price > MAX_PRICE_FALLBACK:
            return None

        if liq < MIN_LIQUIDITY_OBSERVE:
            return None

        log(f"DEX PRICE: {m[:6]}")

        return {
            "price": native_price,
            "liq": liq,
            "source": "dexscreener",
        }
    except Exception:
        return None

async def get_price_info(m, prefer_clean=False):
    candidates = []

    for fn in (jupiter_price, birdeye_price, dexscreener_price):
        try:
            r = await fn(m)
            if r and r.get("price"):
                candidates.append(r)
        except Exception:
            pass

    if prefer_clean:
        for r in candidates:
            if r.get("source") == "jupiter" and sf(r.get("liq", 0), 0.0) >= MIN_LIQUIDITY_TRADE:
                return r
        return None

    for r in candidates:
        if r.get("source") == "jupiter":
            return r

    if candidates:
        return candidates[0]

    last = LAST_PRICE.get(m)
    if last:
        return {
            "price": last,
            "liq": 0,
            "source": LAST_PRICE_SOURCE.get(m, "last_price"),
        }

    return None

async def get_price(m):
    info = await get_price_info(m, prefer_clean=False)
    if not info:
        return None
    return info["price"]


# ================= FEATURES =================

async def features(t):
    m = t.get("mint")
    if not m:
        return None

    # V47/V48/V49/V50: buy-side 只接受乾淨市場
    pinfo = await get_price_info(m, prefer_clean=True)
    if not pinfo:
        return None

    if pinfo.get("source") != "jupiter":
        return None

    if sf(pinfo.get("liq", 0), 0.0) < MIN_LIQUIDITY_TRADE:
        return None

    price = pinfo["price"]
    prev = LAST_PRICE.get(m)

    if prev and prev > 0:
        breakout = (price - prev) / prev
    else:
        breakout = random.uniform(0.004, 0.02)

    breakout = clamp(breakout, -MAX_BREAKOUT_ABS, MAX_BREAKOUT_ABS)
    if abs(breakout) < 0.001:
        breakout = 0.004

    momentum = 0.0
    try:
        await asyncio.sleep(0.35)
        p2 = await get_price(m)
        if price and p2 and p2 > 0:
            momentum = (p2 - price) / price
    except Exception:
        momentum = 0.0

    momentum = clamp(momentum, -MAX_BREAKOUT_ABS, MAX_BREAKOUT_ABS)
    if abs(momentum) < 0.001:
        momentum = breakout * 0.5

    LAST_MOMENTUM[m] = momentum
    LAST_PRICE[m] = price
    LAST_PRICE_SOURCE[m] = pinfo.get("source", "unknown")

    try:
        wallets = await update_token_wallets(m)
    except Exception:
        wallets = []

    wallet_count = len(wallets)
    smart = min(wallet_count / 3.0, 1.0)

    sniper_boost = 0.0
    if t.get("source") == "pumpfun":
        sniper_boost += 0.05
    if t.get("source") == "mempool":
        sniper_boost += 0.08
    if pinfo.get("source") == "jupiter":
        sniper_boost += 0.02

    return {
        "mint": m,
        "price": price,
        "breakout": breakout,
        "momentum": momentum,
        "smart": smart,
        "sniper_boost": sniper_boost,
        "is_new": prev is None,
        "wallet_count": wallet_count,
        "source": t.get("source", "unknown"),
        "meta": t.get("meta", {}),
        "price_source": pinfo.get("source", "unknown"),
        "liq": pinfo.get("liq", 0),
    }


# ================= SCORE / ALLOCATOR =================

def mode(f):
    if f["is_new"]:
        return "sniper"
    if f["smart"] > 0.6:
        return "smart"
    return "momentum"

def score_alpha(f):
    breakout = f.get("breakout", 0.0)
    momentum = f.get("momentum", 0.0)
    smart = f.get("smart", 0.0)
    liq = f.get("liq", 0.0)

    if f.get("price_source") != "jupiter":
        return 0.0, {
            "bscore": 0.0, "mscore": 0.0, "sscore": 0.0,
            "lscore": 0.0, "wscore": 0.0, "nscore": 0.0,
        }

    if sf(liq, 0.0) < MIN_LIQUIDITY_TRADE:
        return 0.0, {
            "bscore": 0.0, "mscore": 0.0, "sscore": 0.0,
            "lscore": 0.0, "wscore": 0.0, "nscore": 0.0,
        }

    if momentum < MIN_CONFIRM_MOMENTUM:
        return 0.0, {
            "bscore": 0.0, "mscore": 0.0, "sscore": 0.0,
            "lscore": 0.0, "wscore": 0.0, "nscore": 0.0,
        }

    if breakout < MIN_CONFIRM_BREAKOUT:
        return 0.0, {
            "bscore": 0.0, "mscore": 0.0, "sscore": 0.0,
            "lscore": 0.0, "wscore": 0.0, "nscore": 0.0,
        }

    if breakout > 0.01 and momentum < 0:
        return 0.0, {
            "bscore": 0.0, "mscore": 0.0, "sscore": 0.0,
            "lscore": 0.0, "wscore": 0.0, "nscore": 0.0,
        }

    bscore = breakout_strength(breakout)
    mscore = momentum_strength(momentum)
    sscore = clamp(sf(smart), 0.0, 1.0) * 0.40
    lscore = min(liq / 1_000_000, 1.0) * 0.12

    wc = f.get("wallet_count", 0)
    if wc >= 3:
        wscore = 0.08
    elif wc >= 2:
        wscore = 0.05
    elif wc >= 1:
        wscore = 0.02
    else:
        wscore = 0.0

    nscore = clamp(sf(f.get("sniper_boost", 0)), 0.0, 0.12)

    score_stat_add("breakout", breakout)
    score_stat_add("momentum", momentum)
    score_stat_add("smart_money", smart)
    score_stat_add("liquidity", liq)
    score_stat_add("wallet_count", wc)
    score_stat_add("price", f.get("price", 0))

    score = (
        bscore * ALPHA_BREAKOUT_WEIGHT +
        mscore * ALPHA_MOMENTUM_WEIGHT +
        sscore * ALPHA_SMART_WEIGHT +
        lscore * ALPHA_LIQ_WEIGHT +
        wscore * ALPHA_WALLET_WEIGHT +
        nscore * 0.05
    )

    mtype = mode(f)
    if mtype == "sniper":
        score *= SNIPER_MULTIPLIER
    elif mtype == "smart":
        score *= SMART_MULTIPLIER
    else:
        score *= MOMENTUM_MULTIPLIER

    return clamp(score, 0.0, MAX_SCORE), {
        "bscore": bscore,
        "mscore": mscore,
        "sscore": sscore,
        "lscore": lscore,
        "wscore": wscore,
        "nscore": nscore,
    }

def source_weight(src):
    s = SOURCE_STATS[src]
    total = s["wins"] + s["losses"]

    mem = 1.0
    if total >= 5:
        winrate = s["wins"] / total if total else 0.0
        if winrate > 0.6:
            mem = 1.12
        elif winrate < 0.3:
            mem = 0.82

    return mem * source_quality(src)

def score_with_allocator(f):
    base, detail = score_alpha(f)
    base *= source_weight(f["source"])

    if TOKEN_TRADE_COUNT[f["mint"]] > 2:
        base *= 0.7

    regime = detect_regime()
    if regime == "bull":
        base *= 1.08
    elif regime == "bear":
        base *= 0.88

    return max(base, 0.0), mode(f), detail

def allocate_size(score, n_candidates):
    if n_candidates <= 0:
        return 0.0

    base = engine.capital / max(n_candidates * 2, 2)

    regime = detect_regime()
    if regime == "bull":
        base *= 1.20
    elif regime == "bear":
        base *= 0.65

    if score > 0.16:
        base *= 2.0
    elif score > 0.14:
        base *= 1.65
    elif score > 0.12:
        base *= 1.15
    else:
        base *= 0.55

    base *= breathing_risk_mult()

    base = min(base, 0.20)
    return min(base, engine.capital * MAX_POSITION_SIZE)


# ================= BUY =================

async def buy(m, f, position_size, mtype, forced=False):
    order_sol = max(position_size, MIN_ORDER_SOL)
    amt_atomic = int(order_sol * SOL_DECIMALS)

    res = await execute_swap(SOL, m, amt_atomic)

    if not res:
        log(f"BUY_EMPTY {m[:6]}")
        engine.stats["errors"] += 1
        return False

    if res.get("error"):
        log(f"BUY_FAIL {m[:6]} {res.get('error')}")
        engine.stats["errors"] += 1
        return False

    out_amount = 0
    try:
        out_amount = int(res.get("quote", {}).get("outAmount") or 0)
    except Exception:
        out_amount = 0

    tx_sig = None
    if isinstance(res.get("result"), str):
        tx_sig = res["result"]
    elif isinstance(res.get("signature"), str):
        tx_sig = res["signature"]

    engine.capital -= position_size
    engine.capital = max(engine.capital, 0.0)

    meta = dict(f.get("meta", {}) or {})
    meta.update({
        "source": f.get("source"),
        "strategy": mtype,
        "forced": forced,
        "breakout": f.get("breakout"),
        "momentum": f.get("momentum"),
        "smart_money": f.get("smart"),
        "liquidity": f.get("liq"),
        "wallet_count": f.get("wallet_count"),
        "price": f.get("price"),
        "score": f.get("_score"),
        "tier": f.get("_tier"),
        "regime": detect_regime(),
    })

    engine.positions.append({
        "mint": m,
        "entry": f["price"],
        "size": position_size,
        "order_sol": order_sol,
        "token_amount_atomic": out_amount,
        "time": now(),
        "mode": mtype,
        "source": f["source"],
        "meta": meta,
        "price_source": f.get("price_source"),
        "liq": f.get("liq", 0),
        "high": f["price"],
        "wallet_count": f.get("wallet_count", 0),
        "tx_buy": tx_sig,
        "forced": forced,
        "paper": bool(res.get("paper")),
        "score": f.get("_score", 0.0),
        "tier": f.get("_tier", "C"),
    })

    LAST_TRADE[m] = now()
    BUY_TIMES.append(now())

    engine.stats["executed"] += 1
    engine.stats["signals"] += 1
    if forced:
        engine.stats["forced_trades"] += 1

    update_open_stats()
    engine.last_signal = f"BUY {m[:6]} {mtype} tier={f.get('_tier','C')} score={f.get('_score', 0):.4f}"
    engine.last_trade = engine.last_signal

    log(f"BUY {m[:6]} {mtype} tier={f.get('_tier','C')} score={f.get('_score', 0):.4f}")
    return True


# ================= SELL =================

async def sell(p, reason, pnl, price):
    m = p["mint"]
    sell_amount = int(p.get("token_amount_atomic") or 0)

    if p.get("paper"):
        res = {"paper": True}
    else:
        if sell_amount <= 0:
            log(f"SELL_NO_AMOUNT {m[:6]}")
            engine.stats["errors"] += 1
            return False
        res = await execute_swap(m, SOL, sell_amount)

    if not res:
        log(f"SELL_EMPTY {m[:6]}")
        engine.stats["errors"] += 1
        return False

    if res.get("error"):
        log(f"SELL_FAIL {m[:6]} {res.get('error')}")
        engine.stats["errors"] += 1
        return False

    if p in engine.positions:
        engine.positions.remove(p)

    pnl = clamp(pnl, -MAX_PNL_ABS, MAX_PNL_ABS)
    realized = p["size"] * (1 + pnl)
    engine.capital += realized

    if engine.capital > MAX_CAPITAL:
        log("CAPITAL_RESET")
        engine.capital = engine.start_capital

    update_peak_capital()

    src = p.get("source", "unknown")
    strategy = p.get("mode", "unknown")

    if pnl > 0:
        engine.stats["wins"] += 1
        source_stat_win(src, pnl)
    else:
        engine.stats["losses"] += 1
        source_stat_loss(src, pnl)

    strategy_stat_update(strategy, pnl)

    push_trade({
        "mint": m,
        "entry": p.get("entry"),
        "exit": price,
        "pnl": pnl,
        "reason": reason,
        "size": p.get("size"),
        "mode": strategy,
        "source": src,
        "price_source": p.get("price_source"),
        "time_open": p.get("time"),
        "time_close": now(),
        "tx_buy": p.get("tx_buy"),
        "meta": p.get("meta", {}),
    })

    update_breathing_state()
    update_open_stats()

    log(f"SELL {m[:6]} {reason} pnl={pnl:.4f}")
    BLACKLIST[m] = now()
    engine.last_trade = f"SELL {m[:6]} {reason} pnl={pnl:.4f}"
    return True


# ================= CHECK SELL =================

async def check_sell(p):
    m = p["mint"]

    price = await get_price(m)
    if price is None:
        return False

    entry = sf(p.get("entry"), 0.0)
    if entry <= 0:
        return False

    hold_sec = now() - sf(p.get("time"), now())

    if price < 1e-8:
        log(f"PRICE_TOO_SMALL_SKIP {m[:6]} {price}")
        return False

    if hold_sec < 8:
        return False

    last = LAST_PRICE.get(m)

    if last and last > 0:
        jump = abs(price - last) / last
        if jump > 0.25:
            log(f"BAD_PRICE_DETECTED {m[:6]} jump={jump:.2f}")
            if hold_sec < 20:
                return False
            else:
                log(f"FORCE_USE_PRICE {m[:6]}")

    pnl = (price - entry) / entry
    pnl = clamp(pnl, -MAX_PNL_ABS, MAX_PNL_ABS)

    p["high"] = max(sf(p.get("high"), entry), price)

    tier = p.get("tier") or (p.get("meta", {}) or {}).get("tier", "C")
    momentum_now = sf(LAST_MOMENTUM.get(m, 0.0), 0.0)
    regime = detect_regime()

    # hard stop
    if pnl <= HARD_STOP_LOSS:
        log(f"HARD_STOP {m[:6]} pnl={pnl:.4f}")
        return await sell(p, "HARD_STOP", pnl, price)

    # force exit
    if hold_sec > FORCE_EXIT_SEC:
        log(f"FORCE_EXIT {m[:6]} pnl={pnl:.4f} hold={hold_sec:.1f}s")
        return await sell(p, "FORCE_EXIT", pnl, price)

    # fast cut
    fast_cut_line = -0.02 if regime != "bear" else -0.015
    if pnl < fast_cut_line and hold_sec > 20:
        log(f"FAST_CUT {m[:6]} pnl={pnl:.4f}")
        return await sell(p, "FAST_CUT", pnl, price)

    # profit momentum hold
    if pnl > 0 and momentum_now > 0.0035:
        return False

    # small red but momentum alive
    if -0.02 < pnl < 0 and momentum_now > 0.0045:
        return False

    # partial TP
    if pnl >= 0.008 and not p.get("tp1_done"):
        p["tp1_done"] = True
        original_size = sf(p.get("size", 0.0), 0.0)
        partial = original_size * 0.5
        p["size"] = original_size * 0.5
        engine.capital += partial
        log(f"PARTIAL_TP {m[:6]} pnl={pnl:.4f}")

    tp = TAKE_PROFIT
    if tier == "A+":
        tp *= 2.2
    elif tier == "A":
        tp *= 1.8

    if regime == "bull":
        tp *= 1.15
    elif regime == "bear":
        tp *= 0.85

    if pnl >= tp:
        return await sell(p, "TP", pnl, price)

    if pnl <= STOP_LOSS:
        await asyncio.sleep(0.4)
        price2 = await get_price(m)
        if price2:
            pnl2 = (price2 - entry) / entry
            pnl2 = clamp(pnl2, -MAX_PNL_ABS, MAX_PNL_ABS)
            if pnl2 <= STOP_LOSS:
                log(f"CONFIRMED_SL {m[:6]} pnl={pnl2:.4f}")
                return await sell(p, "SL", pnl2, price2)
        return False

    dynamic_trailing_gap = TRAILING_GAP
    if tier == "A+":
        dynamic_trailing_gap *= 1.15
    if regime == "bear":
        dynamic_trailing_gap *= 0.85

    if price < p["high"] * (1 - dynamic_trailing_gap):
        log(f"TRAIL_EXIT {m[:6]} pnl={pnl:.4f}")
        return await sell(p, "TRAIL", pnl, price)

    dynamic_hold = MAX_HOLD_SEC
    if regime == "bull":
        dynamic_hold = int(MAX_HOLD_SEC * 1.25)
    elif regime == "bear":
        dynamic_hold = int(MAX_HOLD_SEC * 0.70)

    if hold_sec > dynamic_hold:
        log(
            f"TIME_CHECK {m[:6]} pnl={pnl:.4f} "
            f"momentum={momentum_now:.4f} tier={tier} regime={regime}"
        )

        if tier in {"A", "A+"} and momentum_now > 0.0025 and pnl > 0:
            return False

        if pnl < 0.003:
            return await sell(p, "TIME", pnl, price)

    return False


# ================= PORTFOLIO =================

async def process_candidates(tokens):
    ranked = []
    dyn_threshold = current_dynamic_threshold()
    regime = detect_regime()

    for t in tokens:
        m = t.get("mint")
        if not m:
            continue

        if m in BLACKLIST and now() - BLACKLIST[m] < BLACKLIST_TIME:
            continue

        if now() - LAST_TRADE[m] < 30:
            continue

        f = await features(t)
        if not f:
            continue

        f["source"] = t.get("source", f.get("source", "unknown"))
        f["meta"] = t.get("meta", {})

        sc, mtype, detail = score_with_allocator(f)

        min_threshold = max(dyn_threshold, STRICT_A_TIER_THRESHOLD)
        if regime == "bear":
            min_threshold = max(min_threshold, STRICT_A_TIER_THRESHOLD + 0.01)
        elif regime == "bull":
            min_threshold *= 0.95

        if sc < min_threshold:
            continue

        f["_score"] = sc
        f["_mode"] = mtype

        if sc >= 0.16:
            f["_tier"] = "A+"
        elif sc >= STRICT_A_TIER_THRESHOLD:
            f["_tier"] = "A"
        else:
            f["_tier"] = "B"

        log(
            f"SCORE_DETAIL {m[:6]} "
            f"breakout={f.get('breakout', 0):.4f} "
            f"momentum={f.get('momentum', 0):.4f} "
            f"bscore={detail['bscore']:.4f} "
            f"mscore={detail['mscore']:.4f} "
            f"smart={f.get('smart', 0):.4f} "
            f"sscore={detail['sscore']:.4f} "
            f"liq={sf(f.get('liq', 0)):.2f} "
            f"lscore={detail['lscore']:.4f} "
            f"wallets={f.get('wallet_count', 0)} "
            f"wscore={detail['wscore']:.4f} "
            f"tier={f['_tier']} "
            f"score={sc:.4f}"
        )

        ranked.append(f)

    ranked.sort(key=lambda x: x["_score"], reverse=True)

    top_preview = [f"{x['mint'][:6]}:{x['_score']:.4f}" for x in ranked[:5]]
    if top_preview:
        log(f"TOP_RANKED {' | '.join(top_preview)}")

    return ranked[:10]

async def execute_portfolio(ranked):
    if not ranked:
        log("EXEC_INPUT 0")
        return False

    traded = False
    buys_this_cycle = 0

    ranked = sorted(ranked, key=lambda x: x["_score"], reverse=True)
    ranked = ranked[:TOP_K_PRESELECT]

    log(f"EXEC_INPUT {len(ranked)}")
    log("TOP_RANKED " + " | ".join([f"{r['mint'][:6]}:{r['_score']:.4f}" for r in ranked]))

    in_breathing_cooldown = now() < sf(BREATHING_STATE.get("cooldown_until", 0.0), 0.0)
    if in_breathing_cooldown:
        log(
            f"BREATHING_COOLDOWN risk_mult={breathing_risk_mult():.2f} "
            f"until={int(BREATHING_STATE['cooldown_until'] - now())}s"
        )

    if buy_window_count() >= MAX_BUYS_PER_10MIN:
        log("BUY_RATE_LIMIT")
        return False

    for f in ranked:
        m = f["mint"]

        log(f"EXEC_CANDIDATE {m[:6]} score={f['_score']:.4f} tier={f.get('_tier')} mode={f.get('_mode')}")

        if engine.stats.get("executed", 0) > 10 and engine.stats.get("wins", 0) == 0:
            log("PAUSE_BAD_RUN")
            return False

        if f.get("_tier") not in {"A", "A+"}:
            log(f"SKIP_NON_A_TIER {m[:6]} tier={f.get('_tier')}")
            continue

        if any(p["mint"] == m for p in engine.positions):
            log(f"SKIP_DUP_POS {m[:6]}")
            continue

        if len(engine.positions) >= MAX_POSITIONS:
            log("SKIP_MAX_POSITIONS")
            break

        if exposure() >= engine.capital * MAX_EXPOSURE:
            log("SKIP_MAX_EXPOSURE")
            break

        if now() - LAST_TRADE[m] < TOKEN_COOLDOWN:
            log(f"SKIP_COOLDOWN {m[:6]}")
            continue

        if in_breathing_cooldown:
            if f.get("_tier") != "A+" and sf(f.get("_score"), 0.0) < max(STRICT_A_TIER_THRESHOLD + 0.02, 0.14):
                log(f"SKIP_BREATHING_COOLDOWN {m[:6]} score={f['_score']:.4f} tier={f.get('_tier')}")
                continue

        if SOFT_DISABLE_FILTER:
            ok = True
            log(f"FILTER_BYPASS {m[:6]} SOFT_DISABLE_FILTER=true")
        else:
            try:
                ok, meta = adaptive_filter(f, None, engine.no_trade_cycles)
                log(f"FILTER_META {m[:6]} meta={meta}")
            except Exception as e:
                log(f"FILTER_ERR {m[:6]} {e}")
                ok = False

            if not ok and f["_score"] >= FILTER_SCORE_BYPASS:
                log(f"FILTER_SCORE_BYPASS_HIT {m[:6]} score={f['_score']:.4f}")
                ok = True

        log(f"FILTER_RESULT {m[:6]} ok={ok}")

        if not ok:
            continue

        pos_size = allocate_size(f["_score"], len(ranked))
        if in_breathing_cooldown:
            pos_size *= 0.70

        log(f"TRY_PORTFOLIO {m[:6]} score={f['_score']:.4f} tier={f.get('_tier','?')} mode={f['_mode']}")
        log(f"ALLOC_SIZE {m[:6]} size={pos_size:.4f} capital={engine.capital:.4f}")

        if pos_size <= 0 or engine.capital < pos_size:
            log(f"SKIP_SIZE_OR_CAPITAL {m[:6]}")
            continue

        log(f"PRE_BUY {m[:6]} size={pos_size:.4f}")
        success = await buy(m, f, pos_size, f["_mode"])
        log(f"BUY_RESULT {m[:6]} success={success}")

        if success:
            TOKEN_TRADE_COUNT[m] += 1
            buys_this_cycle += 1
            traded = True

            if buys_this_cycle >= MAX_NEW_BUYS_PER_CYCLE:
                break

            if TOP_N_TO_TRADE <= 1:
                break

    return traded


# ================= METRICS =================

def _avg_stat(name):
    s = SCORE_COMPONENT_STATS.get(name, {"count": 0, "sum": 0.0})
    c = s.get("count", 0)
    if c <= 0:
        return {"count": 0, "avg_score": 0.0}
    return {"count": c, "avg_score": s.get("sum", 0.0) / c}

def _source_perf(src):
    s = SOURCE_STATS.get(src, {"count": 0, "wins": 0, "losses": 0, "total_pnl": 0.0})
    c = s["count"]
    return {
        "count": c,
        "wins": s["wins"],
        "losses": s["losses"],
        "total_pnl": s["total_pnl"],
        "avg_pnl": s["total_pnl"] / c if c else 0.0,
        "win_rate": s["wins"] / c if c else 0.0,
    }

def _strategy_perf(name):
    s = STRATEGY_STATS.get(name, {"count": 0, "wins": 0, "losses": 0, "total_pnl": 0.0})
    c = s["count"]
    return {
        "count": c,
        "wins": s["wins"],
        "losses": s["losses"],
        "total_pnl": s["total_pnl"],
        "avg_pnl": s["total_pnl"] / c if c else 0.0,
        "win_rate": s["wins"] / c if c else 0.0,
    }

def get_metrics():
    start_capital = sf(engine.start_capital, 5.0)
    capital = sf(engine.capital, start_capital)
    peak = max(sf(engine.peak_capital, capital), capital)

    total_return = capital - start_capital
    return_pct = (total_return / start_capital) if start_capital > 0 else 0.0
    drawdown = ((peak - capital) / peak) if peak > 0 else 0.0

    wins = int(engine.stats.get("wins", 0))
    losses = int(engine.stats.get("losses", 0))
    trades = int(engine.stats.get("trades", 0))

    avg_win = 0.0
    avg_loss = 0.0
    win_pnls = [sf(x.get("pnl")) for x in engine.trade_history if sf(x.get("pnl")) > 0]
    loss_pnls = [sf(x.get("pnl")) for x in engine.trade_history if sf(x.get("pnl")) <= 0]

    if win_pnls:
        avg_win = sum(win_pnls) / len(win_pnls)
    if loss_pnls:
        avg_loss = sum(loss_pnls) / len(loss_pnls)

    profit_factor = 0.0
    gross_win = sum(win_pnls)
    gross_loss = abs(sum(loss_pnls))
    if gross_loss > 0:
        profit_factor = gross_win / gross_loss

    tracked_tokens = len(set(LAST_PRICE.keys()))

    source_perf = {k: _source_perf(k) for k in SOURCE_STATS.keys()}
    strategy_perf = {k: _strategy_perf(k) for k in STRATEGY_STATS.keys()}

    best_source = None
    worst_source = None
    if source_perf:
        best_source = max(source_perf.items(), key=lambda x: x[1]["avg_pnl"])[0]
        worst_source = min(source_perf.items(), key=lambda x: x[1]["avg_pnl"])[0]

    return {
        "summary": {
            "capital": capital,
            "start_capital": start_capital,
            "peak_capital": peak,
            "equity_gain": total_return,
            "return_pct": return_pct,
            "drawdown": drawdown,
            "running": bool(engine.running),
            "mode": "REAL" if REAL_TRADING else "PAPER",
            "regime": detect_regime(),
        },
        "performance": {
            "trades": trades,
            "wins": wins,
            "losses": losses,
            "win_rate": (wins / trades) if trades else 0.0,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "total_return": total_return,
        },
        "trading": {
            "signals": engine.stats.get("signals", 0),
            "executed": engine.stats.get("executed", 0),
            "rejected": engine.stats.get("rejected", 0),
            "errors": engine.stats.get("errors", 0),
            "open_positions": len(engine.positions),
            "open_exposure": exposure(),
            "forced_trades": engine.stats.get("forced_trades", 0),
            "no_trade_cycles": engine.no_trade_cycles,
            "breathing_risk_mult": breathing_risk_mult(),
            "breathing_cooldown_left": max(
                0,
                int(sf(BREATHING_STATE.get("cooldown_until", 0.0), 0.0) - now())
            ),
            "buy_window_count": buy_window_count(),
        },
        "positions": engine.positions,
        "recent_trades": engine.trade_history[-20:],
        "logs": engine.logs[-120:],
        "source_stats": source_perf,
        "strategy_stats": strategy_perf,
        "best_source": _source_perf(best_source) if best_source else None,
        "worst_source": _source_perf(worst_source) if worst_source else None,
        "score_component_stats": {
            "breakout": _avg_stat("breakout"),
            "smart_money": _avg_stat("smart_money"),
            "liquidity": _avg_stat("liquidity"),
            "momentum": _avg_stat("momentum"),
            "wallet_count": _avg_stat("wallet_count"),
            "price": _avg_stat("price"),
        },
        "portfolio": {
            "positions_by_source": dict(Counter([p.get("source", "unknown") for p in engine.positions])),
            "positions_by_strategy": dict(Counter([p.get("mode", "unknown") for p in engine.positions])),
            "total_exposure_ratio": exposure() / capital if capital > 0 else 0.0,
            "source_exposure_ratio": {
                k: sum(sf(p.get("size", 0.0)) for p in engine.positions if p.get("source") == k) / capital
                for k in set([p.get("source", "unknown") for p in engine.positions])
            } if capital > 0 else {},
            "strategy_snapshot": {
                k: v for k, v in strategy_perf.items()
            },
        },
        "smart_wallet": {
            "tracked_tokens": tracked_tokens,
            "wallet_count_by_token": {
                k: 1 for k in list(LAST_PRICE.keys())[-100:]
            }
        },
        "open_positions_detail": [
            {
                "mint": p.get("mint"),
                "tier": p.get("tier"),
                "source": p.get("source"),
                "mode": p.get("mode"),
                "entry": p.get("entry"),
                "size": p.get("size"),
                "hold_sec": round(time.time() - sf(p.get("time"), time.time()), 2),
                "high": p.get("high"),
                "price_source": p.get("price_source"),
            }
            for p in (engine.positions or [])
        ]
    }


# ================= LOOP =================

async def main_loop():
    global MEMPOOL_TASK

    ensure_engine()
    log("🚀 V50 FINAL FUSION FUND START")

    if MEMPOOL_TASK is None or MEMPOOL_TASK.done():
        MEMPOOL_TASK = asyncio.create_task(mempool_stream())

    while engine.running:
        try:
            tokens = await fetch_alpha_candidates()

            if not isinstance(tokens, list):
                tokens = []

            tokens = limit_token_frequency(tokens, max_per_token=2)
            tokens = dedup(tokens)
            random.shuffle(tokens)
            tokens = tokens[:MAX_TOKENS_PER_CYCLE]

            log(f"UNIVERSE_SIZE {len(tokens)}")

            if len(tokens) < 3:
                log("SKIP_LOW_UNIVERSE")
                await asyncio.sleep(LOOP_SLEEP_SEC)
                continue

            for p in list(engine.positions):
                await check_sell(p)

            ranked = await process_candidates(tokens)
            log(f"RANKED_SIZE {len(ranked)}")

            traded = await execute_portfolio(ranked)

            if not traded:
                engine.no_trade_cycles += 1
            else:
                engine.no_trade_cycles = 0

            if (
                engine.no_trade_cycles > FORCE_TRADE_AFTER
                and len(engine.positions) < MAX_POSITIONS
                and exposure() < engine.capital * MAX_EXPOSURE
            ):
                current_mints = {p["mint"] for p in engine.positions}
                for f in ranked[:TOP_K_PRESELECT]:
                    if f["mint"] in current_mints:
                        continue
                    if f["_score"] < STRICT_A_TIER_THRESHOLD:
                        continue
                    if f.get("_tier") not in {"A", "A+"}:
                        continue

                    log("FORCE_TRADE")
                    ok = await buy(
                        f["mint"],
                        f,
                        allocate_size(max(f["_score"], STRICT_A_TIER_THRESHOLD), 1),
                        f["_mode"],
                        forced=True,
                    )
                    log(f"FORCE_BUY_RESULT {f['mint'][:6]} success={ok}")
                    if ok:
                        TOKEN_TRADE_COUNT[f["mint"]] += 1
                        engine.no_trade_cycles = 0
                        break

            log("CYCLE_DONE")
            update_open_stats()
            update_peak_capital()

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"ERR {e}")

        await asyncio.sleep(LOOP_SLEEP_SEC)
