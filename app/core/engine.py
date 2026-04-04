# ================= V40 TRUE ALPHA BOOST FULL MARKET =================

import os
import asyncio
import time
import random
import math
from collections import defaultdict, Counter

import httpx

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

MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "1"))
MAX_EXPOSURE = float(os.getenv("MAX_EXPOSURE", "0.50"))
MAX_POSITION_SIZE = float(os.getenv("MAX_POSITION_SIZE", "0.05"))

TAKE_PROFIT = float(os.getenv("TAKE_PROFIT", "0.02"))
STOP_LOSS = float(os.getenv("STOP_LOSS", "-0.01"))
TRAILING_GAP = float(os.getenv("TRAILING_GAP", "0.008"))
MAX_HOLD_SEC = int(os.getenv("MAX_HOLD_SEC", "90"))

TOKEN_COOLDOWN = int(os.getenv("TOKEN_COOLDOWN", "10"))
BLACKLIST_TIME = int(os.getenv("BLACKLIST_TIME", "60"))
FORCE_TRADE_AFTER = int(os.getenv("FORCE_TRADE_AFTER", "15"))
LOOP_SLEEP_SEC = float(os.getenv("LOOP_SLEEP_SEC", "2"))

ENTRY_THRESHOLD = float(os.getenv("ENTRY_THRESHOLD", "0.45"))
FILTER_SCORE_BYPASS = float(os.getenv("FILTER_SCORE_BYPASS", "0.60"))
SOFT_DISABLE_FILTER = os.getenv("SOFT_DISABLE_FILTER", "false").lower() == "true"

MIN_ORDER_SOL = float(os.getenv("MIN_ORDER_SOL", "0.01"))

MIN_PRICE = float(os.getenv("MIN_PRICE", "0.0000000001"))
MAX_PRICE_JUPITER = float(os.getenv("MAX_PRICE_JUPITER", "0.1"))
MAX_PRICE_FALLBACK = float(os.getenv("MAX_PRICE_FALLBACK", "10"))

MAX_BREAKOUT_ABS = float(os.getenv("MAX_BREAKOUT_ABS", "0.20"))
MAX_SCORE = float(os.getenv("MAX_SCORE", "1.5"))
MAX_PNL_ABS = float(os.getenv("MAX_PNL_ABS", "0.2"))
MAX_CAPITAL = float(os.getenv("MAX_CAPITAL", "20"))

MIN_OUT_AMOUNT = int(os.getenv("MIN_OUT_AMOUNT", "300"))
MIN_OUT_AMOUNT_STRICT = int(os.getenv("MIN_OUT_AMOUNT_STRICT", "1000"))
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "6"))
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "").strip()

MIN_UNIVERSE = int(os.getenv("MIN_UNIVERSE", "5"))
BOOT_SYNTHETIC_UNIVERSE = os.getenv("BOOT_SYNTHETIC_UNIVERSE", "true").lower() == "true"

ADAPTIVE_THRESHOLD_MIN = float(os.getenv("ADAPTIVE_THRESHOLD_MIN", "0.005"))
ADAPTIVE_THRESHOLD_MAX = float(os.getenv("ADAPTIVE_THRESHOLD_MAX", "0.08"))

TOP_N_TO_TRADE = int(os.getenv("TOP_N_TO_TRADE", "1"))
MAX_TOKENS_PER_CYCLE = int(os.getenv("MAX_TOKENS_PER_CYCLE", "25"))


# ================= RUNTIME MEMORY =================

LAST_TRADE = defaultdict(float)
LAST_PRICE = {}
LAST_MOMENTUM = {}

TOKEN_TRADE_COUNT = defaultdict(int)
BLACKLIST = {}

SOURCE_STATS = defaultdict(lambda: {
    "count": 0,
    "wins": 0,
    "losses": 0,
    "total_pnl": 0.0,
})


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
    engine.logs = engine.logs[-600:]


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
    if engine.no_trade_cycles > 30:
        base *= 0.5
    elif engine.no_trade_cycles > 15:
        base *= 0.7
    if engine.stats.get("executed", 0) == 0:
        base *= 0.9
    return clamp(base, ADAPTIVE_THRESHOLD_MIN, ADAPTIVE_THRESHOLD_MAX)

def normalize_liq(liq: float) -> float:
    liq = max(sf(liq), 0.0)
    if liq <= 0:
        return 0.0
    x = math.log10(liq + 1.0)
    score = (x - 2.0) / 7.0
    score = clamp(score, 0.0, 1.0)
    return score * 0.55

def normalize_wallets(n: int) -> float:
    n = max(int(n), 0)
    if n == 0:
        return 0.0
    if n == 1:
        return 0.02
    if n <= 3:
        return 0.08
    if n <= 5:
        return 0.15
    if n <= 10:
        return 0.24
    return 0.32

def breakout_strength(b: float) -> float:
    b = clamp(sf(b), -MAX_BREAKOUT_ABS, MAX_BREAKOUT_ABS)
    if b <= 0:
        return 0.0
    return min(b / 0.05, 1.0) * 0.35

def momentum_strength(m: float) -> float:
    m = clamp(sf(m), -MAX_BREAKOUT_ABS, MAX_BREAKOUT_ABS)
    if m <= 0:
        return 0.0
    return min(m / 0.05, 1.0) * 0.30


# ================= HTTP =================

async def http_get(url, params=None, headers=None, timeout=HTTP_TIMEOUT):
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(url, params=params, headers=headers)
            r.raise_for_status()
            return r.json()
    except Exception:
        return None


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

async def fetch_pumpfun_candidates(limit=20):
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
        fetch_dexscreener_candidates("SOL"),
        fetch_dexscreener_candidates("USDC"),
        fetch_dexscreener_candidates("BONK"),
        return_exceptions=True,
    )

    merged = []
    for r in results:
        if isinstance(r, list):
            merged.extend(r)

    if len(merged) < MIN_UNIVERSE and BOOT_SYNTHETIC_UNIVERSE:
        log(f"LOW_UNIVERSE_BOOT {len(merged)}")
        for i in range(10):
            merged.append({
                "mint": f"SIM{i}{random.randint(1000,9999)}",
                "source": "synthetic",
                "meta": {},
            })

    return merged

def source_quality(source: str) -> float:
    if source == "pumpfun":
        return 1.20
    if source == "dexscreener":
        return 1.05
    if source == "fusion":
        return 1.08
    if source == "jupiter":
        return 0.95
    if source == "synthetic":
        return 0.30
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

        if native_price > 10:
            log(f"DEX_SKIP_BAD_UNIT {m[:6]} {native_price}")
            return None

        if native_price <= 0 or native_price > MAX_PRICE_FALLBACK:
            return None

        log(f"DEX PRICE: {m[:6]}")

        return {
            "price": native_price,
            "liq": sf((pair.get("liquidity", {}) or {}).get("usd", 0)),
            "source": "dexscreener",
        }
    except Exception:
        return None

async def get_price_info(m):
    for fn in (jupiter_price, birdeye_price, dexscreener_price):
        try:
            r = await fn(m)
            if r and r.get("price"):
                return r
        except Exception:
            pass

    last = LAST_PRICE.get(m)
    if last:
        return {
            "price": last,
            "liq": 0,
            "source": "last_price",
        }

    return None

async def get_price(m):
    info = await get_price_info(m)
    if not info:
        return None
    return info["price"]


# ================= FEATURES =================

async def features(t):
    m = t.get("mint")
    if not m:
        return None

    pinfo = await get_price_info(m)
    if not pinfo:
        return None

    price = pinfo["price"]
    prev = LAST_PRICE.get(m)

    if prev and prev > 0:
        breakout = (price - prev) / prev
    else:
        breakout = random.uniform(0.001, 0.02)

    breakout = clamp(breakout, -MAX_BREAKOUT_ABS, MAX_BREAKOUT_ABS)

    momentum = 0.0
    try:
        await asyncio.sleep(0.4)
        p2 = await get_price(m)
        if price and p2:
            momentum = (p2 - price) / price
    except Exception:
        momentum = 0.0

    momentum = clamp(momentum, -MAX_BREAKOUT_ABS, MAX_BREAKOUT_ABS)
    LAST_MOMENTUM[m] = momentum
    LAST_PRICE[m] = price

    try:
        wallets = await update_token_wallets(m)
    except Exception:
        wallets = []

    wallet_count = len(wallets)
    smart = min(wallet_count / 3.0, 1.0)

    sniper_boost = 0.0
    if t.get("source") == "pumpfun":
        sniper_boost += 0.05
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
    breakout = f.get("breakout", 0)
    momentum = f.get("momentum", 0)
    smart = f.get("smart", 0)
    liq = f.get("liq", 0)

    bscore = breakout_strength(breakout)
    mscore = momentum_strength(momentum)
    sscore = clamp(sf(smart), 0.0, 1.0) * 0.28
    lscore = min(liq / 1_000_000, 1.0) * 0.12
    wscore = 0.05 if f.get("wallet_count", 0) >= 2 else 0.0
    nscore = clamp(sf(f.get("sniper_boost", 0)), 0.0, 0.12)

    # fake breakout filter: breakout 有但 momentum 不跟
    if breakout > 0.01 and momentum <= 0:
        bscore *= 0.35

    score = (
        bscore * 0.35 +
        mscore * 0.25 +
        sscore * 0.20 +
        lscore * 0.15 +
        wscore +
        nscore * 0.05
    )

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
            mem = 1.15
        elif winrate < 0.3:
            mem = 0.85

    return mem * source_quality(src)

def score_with_allocator(f):
    base, detail = score_alpha(f)
    base *= source_weight(f["source"])

    if TOKEN_TRADE_COUNT[f["mint"]] > 2:
        base *= 0.7

    return max(base, 0.0), mode(f), detail

def allocate_size(score, n_candidates):
    if n_candidates <= 0:
        return 0.0

    bucket = engine.capital / max(n_candidates * 2, 2)

    if score > 0.75:
        bucket *= 1.20
    elif score > 0.60:
        bucket *= 1.00
    else:
        bucket *= 0.70

    bucket = min(bucket, 0.05)
    return min(bucket, engine.capital * MAX_POSITION_SIZE)


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
    })

    LAST_TRADE[m] = now()
    engine.stats["executed"] += 1
    engine.stats["signals"] += 1
    if forced:
        engine.stats["forced_trades"] += 1

    update_open_stats()

    engine.last_signal = f"BUY {m[:6]} {mtype} score={f.get('_score', 0):.4f}"
    engine.last_trade = engine.last_signal

    log(f"BUY {m[:6]} {mtype} score={f.get('_score', 0):.4f}")
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
    if pnl > 0:
        engine.stats["wins"] += 1
        source_stat_win(src, pnl)
    else:
        engine.stats["losses"] += 1
        source_stat_loss(src, pnl)

    push_trade({
        "mint": m,
        "entry": p.get("entry"),
        "exit": price,
        "pnl": pnl,
        "reason": reason,
        "size": p.get("size"),
        "mode": p.get("mode"),
        "source": src,
        "price_source": p.get("price_source"),
        "time_open": p.get("time"),
        "time_close": now(),
        "tx_buy": p.get("tx_buy"),
        "meta": p.get("meta", {}),
    })

    update_open_stats()
    log(f"SELL {m[:6]} {reason} pnl={pnl:.4f}")
    BLACKLIST[m] = now()
    engine.last_trade = f"SELL {m[:6]} {reason} pnl={pnl:.4f}"
    return True

async def check_sell(p):
    price = await get_price(p["mint"])
    if price is None:
        return False

    entry = sf(p.get("entry"), 0.0)
    if entry <= 0:
        return False

    pnl = (price - entry) / entry
    pnl = clamp(pnl, -MAX_PNL_ABS, MAX_PNL_ABS)

    p["high"] = max(sf(p.get("high"), entry), price)

    reason = None
    if pnl >= TAKE_PROFIT:
        reason = "TP"
    elif pnl <= STOP_LOSS:
        reason = "SL"
    elif price < p["high"] * (1 - TRAILING_GAP):
        reason = "TRAIL"
    elif now() - sf(p.get("time"), now()) > MAX_HOLD_SEC:
        reason = "TIME"

    if reason:
        return await sell(p, reason, pnl, price)

    return False


# ================= PORTFOLIO =================

async def process_candidates(tokens):
    ranked = []
    dyn_threshold = current_dynamic_threshold()

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
            f"score={sc:.4f}"
        )

        if sc < dyn_threshold:
            continue

        f["_score"] = sc
        f["_mode"] = mtype
        ranked.append(f)

    ranked.sort(key=lambda x: x["_score"], reverse=True)

    top_preview = [f"{x['mint'][:6]}:{x['_score']:.4f}" for x in ranked[:5]]
    if top_preview:
        log(f"TOP_RANKED {' | '.join(top_preview)}")

    return ranked[:10]

async def execute_portfolio(ranked):
    if not ranked:
        return False

    traded = False
    ranked = ranked[:TOP_N_TO_TRADE]

    for f in ranked:
        m = f["mint"]

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

        if SOFT_DISABLE_FILTER:
            ok = True
        else:
            ok, _ = adaptive_filter(f, None, engine.no_trade_cycles)
            if not ok and f["_score"] >= FILTER_SCORE_BYPASS:
                ok = True

        log(f"FILTER_RESULT {m[:6]} ok={ok}")

        if not ok:
            continue

        if f["_score"] < 0.05:
            log(f"SKIP_LOW_SCORE {m[:6]}")
            continue

        pos_size = allocate_size(f["_score"], len(ranked))
        log(f"TRY_PORTFOLIO {m[:6]} score={f['_score']:.4f} mode={f['_mode']}")
        log(f"ALLOC_SIZE {m[:6]} size={pos_size:.4f} capital={engine.capital:.4f}")

        if pos_size <= 0 or engine.capital < pos_size:
            log(f"SKIP_SIZE_OR_CAPITAL {m[:6]}")
            continue

        success = await buy(m, f, pos_size, f["_mode"])
        log(f"BUY_RESULT {m[:6]} success={success}")

        if success:
            TOKEN_TRADE_COUNT[m] += 1
            traded = True

    return traded


# ================= METRICS =================

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
        },
        "positions": engine.positions,
        "recent_trades": engine.trade_history[-20:],
        "logs": engine.logs[-120:],
        "source_stats": dict(SOURCE_STATS),
        "portfolio": {
            "tracked_tokens": tracked_tokens,
            "positions_by_source": dict(Counter([p.get("source", "unknown") for p in engine.positions])),
            "positions_by_strategy": dict(Counter([p.get("mode", "unknown") for p in engine.positions])),
        }
    }


# ================= LOOP =================

async def main_loop():
    ensure_engine()
    log("🚀 V40 TRUE ALPHA BOOST START")

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

                for f in ranked[:TOP_N_TO_TRADE]:
                    if f["mint"] in current_mints:
                        continue

                    log("FORCE_TRADE")
                    ok = await buy(
                        f["mint"],
                        f,
                        allocate_size(max(f["_score"], 0.05), 1),
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
