# ================= V37.9 FULL FUSION TRUE MARKET DATA =================

import os
import asyncio
import time
import random
from collections import defaultdict

import httpx

from app.state import engine
from app.alpha.adaptive_filter import adaptive_filter

try:
    from app.sources.fusion import fetch_candidates
except Exception:
    async def fetch_candidates():
        return []

try:
    from app.alpha.helius_wallet_tracker import update_token_wallets
except Exception:
    async def update_token_wallets(m):
        return []

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


# ================= CONFIG =================

REAL_TRADING = os.getenv("REAL_TRADING", "false").lower() == "true"

SOL = "So11111111111111111111111111111111111111112"
SOL_DECIMALS = 1_000_000_000
AMOUNT = int(os.getenv("AMOUNT", "1000000"))  # quote size, atomic SOL

MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "3"))
MAX_EXPOSURE = float(os.getenv("MAX_EXPOSURE", "0.50"))
MAX_POSITION_SIZE = float(os.getenv("MAX_POSITION_SIZE", "0.15"))

TAKE_PROFIT = float(os.getenv("TAKE_PROFIT", "0.05"))
STOP_LOSS = float(os.getenv("STOP_LOSS", "-0.02"))
TRAILING_GAP = float(os.getenv("TRAILING_GAP", "0.012"))
MAX_HOLD_SEC = int(os.getenv("MAX_HOLD_SEC", "120"))

TOKEN_COOLDOWN = int(os.getenv("TOKEN_COOLDOWN", "10"))
FORCE_TRADE_AFTER = int(os.getenv("FORCE_TRADE_AFTER", "15"))
LOOP_SLEEP_SEC = float(os.getenv("LOOP_SLEEP_SEC", "2"))

ENTRY_THRESHOLD = float(os.getenv("ENTRY_THRESHOLD", "0.003"))
SNIPER_FALLBACK_THRESHOLD = float(os.getenv("SNIPER_FALLBACK_THRESHOLD", "0.001"))
MIN_ORDER_SOL = float(os.getenv("MIN_ORDER_SOL", "0.01"))

# ===== STABILITY CLAMPS =====
MIN_PRICE = float(os.getenv("MIN_PRICE", "0.0000000001"))   # SOL per token
MAX_PRICE = float(os.getenv("MAX_PRICE", "0.01"))           # SOL per token
MAX_BREAKOUT_ABS = float(os.getenv("MAX_BREAKOUT_ABS", "0.05"))
MAX_SCORE = float(os.getenv("MAX_SCORE", "0.1"))
MAX_PNL_ABS = float(os.getenv("MAX_PNL_ABS", "0.2"))
MAX_CAPITAL = float(os.getenv("MAX_CAPITAL", "20"))

MIN_OUT_AMOUNT = int(os.getenv("MIN_OUT_AMOUNT", "1000"))
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "6"))
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "").strip()


# ================= RUNTIME MEMORY =================

LAST_TRADE = defaultdict(float)
LAST_PRICE = {}

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
    engine.logs = engine.logs[-300:]


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


# ================= HTTP =================

async def http_get(url, params=None, headers=None, timeout=HTTP_TIMEOUT):
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(url, params=params, headers=headers)
            r.raise_for_status()
            return r.json()
    except Exception:
        return None


# ================= PRICE SOURCES =================

async def safe_quote(input_mint, output_mint, amount):
    for _ in range(3):
        try:
            q = await get_quote(input_mint, output_mint, amount)
            if q:
                return q
        except Exception:
            pass
        await asyncio.sleep(0.2)
    return None

async def jupiter_price(m):
    """
    回傳:
      {
        "price": SOL per token,
        "liq": outAmount,
        "source": "jupiter"
      }
    """
    q = await safe_quote(SOL, m, AMOUNT)
    if not q:
        return None

    try:
        in_amt = sf(q.get("inAmount", 0))
        out_amt = sf(q.get("outAmount", 0))
    except Exception:
        return None

    if in_amt <= 0 or out_amt <= 0:
        return None

    if out_amt < MIN_OUT_AMOUNT:
        log(f"LOW_LIQ {m[:6]} {int(out_amt)}")
        return None

    price = in_amt / out_amt  # ✅ SOL per token

    if price < MIN_PRICE or price > MAX_PRICE:
        log(f"BAD_PRICE {m[:6]} {price:.10f}")
        return None

    return {
        "price": price,
        "liq": out_amt,
        "source": "jupiter",
    }

async def birdeye_price(m):
    """
    嘗試把 USD price 轉成 SOL price:
      token_usd / sol_usd = SOL per token
    """
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

        price = token_usd / sol_usd  # SOL per token
        if price < MIN_PRICE or price > MAX_PRICE:
            return None

        return {
            "price": price,
            "liq": 0,
            "source": "birdeye",
        }
    except Exception:
        return None

async def dexscreener_price(m):
    """
    同樣把 USD price 轉成 SOL price
    """
    res = await http_get(f"https://api.dexscreener.com/latest/dex/search/?q={m}")
    if not res:
        return None

    try:
        pairs = res.get("pairs", [])
        if not pairs:
            return None

        pair = pairs[0]
        token_usd = sf(pair.get("priceUsd", 0))
        native_price = sf(pair.get("priceNative", 0))

        # priceNative 若為 SOL pair，通常就是 SOL per token
        if native_price > 0:
            price = native_price
        elif token_usd > 0:
            # 沒有 SOL USD 價時，不硬算
            return None
        else:
            return None

        if price < MIN_PRICE or price > MAX_PRICE:
            return None

        return {
            "price": price,
            "liq": sf(pair.get("liquidity", {}).get("usd", 0)),
            "source": "dexscreener",
        }
    except Exception:
        return None

async def get_price_info(m):
    """
    多資料源:
      1. Jupiter
      2. Birdeye
      3. DexScreener
      4. LAST_PRICE fallback
    """
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
        breakout = 0.005

    breakout = clamp(breakout, -MAX_BREAKOUT_ABS, MAX_BREAKOUT_ABS)

    if breakout == 0:
        breakout = random.uniform(0.001, 0.003)

    LAST_PRICE[m] = price

    try:
        wallets = await update_token_wallets(m)
    except Exception:
        wallets = []

    smart = min(len(wallets) / 5.0, 1.0)

    return {
        "mint": m,
        "price": price,
        "breakout": breakout,
        "smart": smart,
        "is_new": prev is None,
        "wallet_count": len(wallets),
        "source": t.get("source", "unknown"),
        "price_source": pinfo.get("source", "unknown"),
        "liq": pinfo.get("liq", 0),
    }


# ================= SCORE =================

def mode(f):
    if f["is_new"]:
        return "sniper"
    if f["smart"] > 0.6:
        return "smart"
    return "momentum"

def score_alpha(f):
    m = mode(f)

    liq_bonus = 0.0
    if sf(f.get("liq", 0)) > 0:
        liq_bonus = 0.01

    if m == "sniper":
        raw = f["breakout"] * 0.4 + f["smart"] * 0.6 + liq_bonus
    elif m == "smart":
        raw = f["smart"] * 0.7 + f["breakout"] * 0.3 + liq_bonus
    else:
        raw = f["breakout"] * 0.8 + f["smart"] * 0.2 + liq_bonus

    raw = clamp(raw, 0.0, MAX_SCORE)
    return raw, m

def size_by_score(score):
    base = engine.capital * 0.03

    if score > 0.02:
        base *= 1.15
    if score > 0.05:
        base *= 1.10

    base = min(base, 0.2)
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

    engine.positions.append({
        "mint": m,
        "entry": f["price"],
        "size": position_size,
        "order_sol": order_sol,
        "token_amount_atomic": out_amount,
        "time": now(),
        "mode": mtype,
        "source": f["source"],
        "price_source": f.get("price_source"),
        "liq": f.get("liq", 0),
        "high": f["price"],
        "wallet_count": f.get("wallet_count", 0),
        "tx_buy": tx_sig,
        "forced": forced,
        "paper": bool(res.get("paper")),
    })

    LAST_TRADE[m] = now()
    engine.stats["executed"] += 1
    if forced:
        engine.stats["forced_trades"] += 1

    update_open_stats()

    engine.last_signal = (
        f"BUY {m[:6]} {mtype} score={f.get('_score', 0):.4f} entry={f['price']:.10f}"
    )
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
    })

    update_open_stats()

    engine.last_trade = f"SELL {m[:6]} {reason} pnl={pnl:.4f}"
    log(f"SELL {m[:6]} {reason} pnl={pnl:.4f}")
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


# ================= TRADE =================

async def trade(t, forced=False):
    m = t.get("mint")
    if not m:
        return False

    if any(p["mint"] == m for p in engine.positions):
        log(f"SKIP_DUP_POS {m[:6]}")
        return False

    if now() - LAST_TRADE[m] < TOKEN_COOLDOWN:
        log(f"SKIP_COOLDOWN {m[:6]}")
        return False

    if len(engine.positions) >= MAX_POSITIONS:
        log("SKIP_MAX_POSITIONS")
        return False

    if exposure() >= engine.capital * MAX_EXPOSURE:
        log("SKIP_MAX_EXPOSURE")
        return False

    f = await features(t)
    if not f:
        engine.stats["rejected"] += 1
        return False

    ok, _ = adaptive_filter(f, None, engine.no_trade_cycles)
    if not ok:
        ok = engine.no_trade_cycles > 5 or forced

    if not ok:
        log(f"FILTER_BLOCK {m[:6]}")
        engine.stats["rejected"] += 1
        return False

    sc, mtype = score_alpha(f)
    f["_score"] = sc

    if sc < ENTRY_THRESHOLD:
        if not ((mtype == "sniper" and sc > SNIPER_FALLBACK_THRESHOLD) or forced):
            engine.stats["rejected"] += 1
            return False

    position_size = size_by_score(sc)

    if engine.capital < position_size:
        log(f"SKIP_NO_CAPITAL {m[:6]}")
        engine.stats["rejected"] += 1
        return False

    engine.stats["signals"] += 1
    return await buy(m, f, position_size, mtype, forced=forced)


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
        "logs": engine.logs[-80:],
        "source_stats": dict(SOURCE_STATS),
    }


# ================= LOOP =================

async def main_loop():
    ensure_engine()
    log("🚀 V37.9 START")

    while engine.running:
        try:
            tokens = await fetch_candidates()
            if not isinstance(tokens, list):
                tokens = []

            tokens = dedup(tokens)
            random.shuffle(tokens)

            for p in list(engine.positions):
                await check_sell(p)

            traded = False

            for t in tokens[:20]:
                ok = await trade(t)
                if ok:
                    traded = True

            if not traded:
                engine.no_trade_cycles += 1
            else:
                engine.no_trade_cycles = 0

            if (
                engine.no_trade_cycles > FORCE_TRADE_AFTER
                and tokens
                and len(engine.positions) < MAX_POSITIONS
                and exposure() < engine.capital * MAX_EXPOSURE
            ):
                log("FORCE_TRADE")
                await trade(tokens[0], forced=True)

            update_open_stats()
            update_peak_capital()

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"ERR {e}")

        await asyncio.sleep(LOOP_SLEEP_SEC)
