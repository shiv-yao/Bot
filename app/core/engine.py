# ================= V34.1 FULL FUSION FUND MODE =================

import asyncio
import time
import random
from collections import defaultdict

from app.state import engine
from app.alpha.adaptive_filter import adaptive_filter

try:
    from app.sources.fusion import fetch_candidates
except Exception:
    async def fetch_candidates():
        return []

try:
    from app.data.market import get_quote, looks_like_solana_mint
except Exception:
    async def get_quote(a, b, c):
        return None

    def looks_like_solana_mint(x):
        return True

try:
    from app.alpha.helius_wallet_tracker import update_token_wallets
except Exception:
    async def update_token_wallets(m):
        return []


# ================= CONFIG =================
MAX_POSITIONS = 3
MAX_EXPOSURE = 0.40
MAX_POSITION_SIZE = 0.15

TAKE_PROFIT = 0.08
STOP_LOSS = -0.03
HARD_STOP_LOSS = -0.15
TRAILING_GAP = 0.015
MAX_HOLD_SEC = 60

TOKEN_COOLDOWN = 10
GLOBAL_COOLDOWN = 3

SOL = "So11111111111111111111111111111111111111112"
AMOUNT = 1_000_000

MIN_SCORE = 0.55
MIN_BREAKOUT = 0.01
MIN_LIQUIDITY = 0.01

LAST_TRADE = defaultdict(float)
LAST_PRICE = {}
LAST_GLOBAL_TRADE_TS = 0.0

# 🧠 FUND MEMORY
SOURCE_STATS = defaultdict(
    lambda: {
        "wins": 0,
        "losses": 0,
        "pnl": 0.0,
        "disabled": False,
    }
)


# ================= ENGINE =================
def ensure_engine():
    engine.positions = getattr(engine, "positions", [])
    engine.trade_history = getattr(engine, "trade_history", [])
    engine.logs = getattr(engine, "logs", [])

    engine.capital = getattr(engine, "capital", 5.0)
    engine.start_capital = getattr(engine, "start_capital", engine.capital)
    engine.peak_capital = getattr(engine, "peak_capital", engine.capital)

    engine.running = getattr(engine, "running", True)
    engine.no_trade_cycles = getattr(engine, "no_trade_cycles", 0)
    engine.last_signal = getattr(engine, "last_signal", "")
    engine.last_trade = getattr(engine, "last_trade", "")
    engine.regime = getattr(engine, "regime", "unknown")

    engine.stats = getattr(
        engine,
        "stats",
        {
            "signals": 0,
            "executed": 0,
            "wins": 0,
            "losses": 0,
            "errors": 0,
            "rejected": 0,
        },
    )


# ================= LOG =================
def log(msg):
    print(msg)
    engine.logs.append(str(msg))
    engine.logs = engine.logs[-300:]


# ================= HELP =================
def sf(x):
    try:
        return float(x)
    except Exception:
        return 0.0


def exposure():
    return sum(sf(p.get("size", 0)) for p in engine.positions)


def risk():
    if engine.peak_capital > 0:
        dd = (engine.capital - engine.peak_capital) / engine.peak_capital

        if dd < -0.35:
            log("🛑 ENGINE HARD STOP")
            engine.running = False
            return False

        if dd < -0.15:
            log("⚠️ RISK MODE")
            engine.regime = "risk_off"
        else:
            engine.regime = "normal"

    return True


def market_ok():
    # 保留之前 no_trade_cycles 狀態，同時避免過度頻繁出手
    # 這裡不做過度封鎖，只在冷機啟動時稍微保守
    if engine.stats.get("executed", 0) == 0 and engine.no_trade_cycles < 2:
        return False
    return True


# ================= PRICE =================
async def safe_quote(i, o, a):
    for _ in range(3):
        try:
            q = await get_quote(i, o, a)
            if q and q.get("outAmount"):
                return q
        except Exception as e:
            log(f"QUOTE_ERR {str(e)[:80]}")
        await asyncio.sleep(0.2 + random.random() * 0.2)
    return None


async def get_price(m):
    if not looks_like_solana_mint(m):
        return None

    q = await safe_quote(SOL, m, AMOUNT)
    if not q:
        return None

    out = sf(q.get("outAmount", 0))
    if out <= 0:
        return None

    return out / 1e6, q


# ================= FUND BRAIN =================
def source_total(src):
    s = SOURCE_STATS[src]
    return s["wins"] + s["losses"]


def source_winrate(src):
    total = source_total(src)
    if total <= 0:
        return 0.0
    return SOURCE_STATS[src]["wins"] / total


def source_ok(src):
    s = SOURCE_STATS[src]
    total = s["wins"] + s["losses"]

    # 冷啟動先允許
    if total < 5:
        return True

    winrate = s["wins"] / total

    # V34：虧損來源自動停用
    if winrate < 0.4 and s["pnl"] < -0.05:
        s["disabled"] = True

    return not s["disabled"]


def source_weight(src):
    s = SOURCE_STATS[src]
    total = s["wins"] + s["losses"]

    if total < 3:
        return 1.0

    winrate = s["wins"] / max(total, 1)

    if winrate > 0.60:
        return 1.5
    elif winrate < 0.40:
        return 0.5

    return 1.0


# ================= FEATURES =================
async def features(t):
    m = t["mint"]
    src = t.get("source", "unknown")

    if not looks_like_solana_mint(m):
        log(f"BAD_MINT {m}")
        return None

    if not source_ok(src):
        log(f"KILL_SOURCE {src}")
        return None

    try:
        wallets = await update_token_wallets(m)
    except Exception:
        wallets = []

    if wallets is None:
        wallets = []

    data = await get_price(m)
    if not data:
        log(f"FEATURE_PRICE_FAIL {m[:6]}")
        return None

    price, q = data
    prev = LAST_PRICE.get(m)

    # ===== 防垃圾價格 =====
    if price < 1e-8:
        log(f"FEATURE_BAD_PRICE {m[:6]} price={price}")
        return None

    # ===== breakout / momentum =====
    if prev and prev > 0:
        raw = (price - prev) / prev
        breakout = min(max(raw * 4.0, 0.0), 1.0)
    else:
        breakout = 0.01

    # ===== 防假跳動 / 極端 impact =====
    if prev and prev > 0:
        impact = abs(price - prev) / prev
        if impact > 0.5:
            LAST_PRICE[m] = price
            log(f"FEATURE_IMPACT_FAIL {m[:6]} impact={impact:.4f}")
            return None

    LAST_PRICE[m] = price

    # ===== liquidity 正規化 =====
    liq_raw = sf(q.get("outAmount", 0))
    liquidity = min(liq_raw / 1e6, 1.0)

    # Dex fallback 額外防呆
    if q.get("source") == "dexscreener":
        if sf(q.get("liquidityUsd", 0)) < 20000:
            log(f"FEATURE_DEX_LIQUSD_FAIL {m[:6]} liqUsd={q.get('liquidityUsd', 0)}")
            return None

    smart = min(len(wallets) / 5, 1.0)

    # V34：對弱 source 再嚴一點
    if src == "dex" and smart < 0.3:
        log(f"FEATURE_SOURCE_SMART_FAIL {m[:6]} smart={smart:.3f}")
        return None

    if breakout < MIN_BREAKOUT:
        log(f"FEATURE_BREAKOUT_FAIL {m[:6]} breakout={breakout:.4f}")
        return None

    if liquidity < MIN_LIQUIDITY:
        log(f"FEATURE_LIQ_FAIL {m[:6]} liq={liquidity:.4f}")
        return None

    return {
        "mint": m,
        "source": src,
        "breakout": breakout,
        "smart_money": smart,
        "liquidity": liquidity,
        "price": price,
    }


# ================= SCORE =================
def score_alpha(f):
    base = (
        f["breakout"] * 0.5 +
        f["smart_money"] * 0.3 +
        f["liquidity"] * 0.2
    )
    score = base * source_weight(f["source"])
    return min(score, 1.0)


# ================= SIZE / ALLOCATOR =================
def size(score, src):
    # V29.6 風控 + V34 allocator
    base = engine.capital * 0.06

    if score > 0.7:
        base *= 2.0
    elif score > 0.6:
        base *= 1.5
    elif score > 0.55:
        base *= 1.2

    # source 績效好的多配一點
    sw = source_weight(src)
    base *= sw

    # risk off 模式縮倉
    if getattr(engine, "regime", "normal") == "risk_off":
        base *= 0.5

    return min(base, engine.capital * MAX_POSITION_SIZE)


# ================= SELL =================
async def check_sell(p):
    data = await get_price(p["mint"])
    if not data:
        log(f"SELL_PRICE_FAIL {p['mint'][:6]}")
        return

    price, _ = data
    entry = sf(p.get("entry", 0))
    if entry <= 0:
        log(f"SELL_ENTRY_FAIL {p['mint'][:6]}")
        return

    pnl = (price - entry) / entry
    held = time.time() - p["time"]

    reason = None

    if pnl >= TAKE_PROFIT:
        reason = "TP"
    elif pnl <= HARD_STOP_LOSS:
        reason = "HARD_SL"
    elif pnl <= STOP_LOSS:
        reason = "SL"
    elif pnl < p.get("peak", 0.0) - TRAILING_GAP:
        reason = "TRAIL"
    elif held > MAX_HOLD_SEC and pnl < -0.002:
        reason = "TIME"

    if pnl > p.get("peak", 0.0):
        p["peak"] = pnl

    if not reason:
        return

    if p in engine.positions:
        engine.positions.remove(p)

    engine.capital += p["size"] * (1 + pnl)

    src = p["source"]
    if pnl > 0:
        SOURCE_STATS[src]["wins"] += 1
        engine.stats["wins"] += 1
    else:
        SOURCE_STATS[src]["losses"] += 1
        engine.stats["losses"] += 1

    SOURCE_STATS[src]["pnl"] += pnl

    engine.trade_history.append(
        {
            "mint": p["mint"],
            "pnl": pnl,
            "reason": reason,
            "timestamp": time.time(),
            "meta": {"source": src},
        }
    )
    engine.trade_history = engine.trade_history[-500:]

    if engine.capital > engine.peak_capital:
        engine.peak_capital = engine.capital

    engine.last_trade = f"{p['mint'][:6]} {reason} pnl={pnl:.4f}"
    log(f"SELL {p['mint'][:6]} {reason} pnl={pnl:.4f}")


# ================= TRADE =================
async def trade(t):
    global LAST_GLOBAL_TRADE_TS

    m = t["mint"]
    src = t.get("source", "unknown")

    if any(p["mint"] == m for p in engine.positions):
        log(f"SKIP_HELD {m[:6]}")
        return False

    now = time.time()

    if now - LAST_TRADE[m] < TOKEN_COOLDOWN:
        log(f"SKIP_COOLDOWN {m[:6]}")
        return False

    if now - LAST_GLOBAL_TRADE_TS < GLOBAL_COOLDOWN:
        log("SKIP_GLOBAL_COOLDOWN")
        return False

    if len(engine.positions) >= MAX_POSITIONS:
        log("SKIP_MAX_POSITIONS")
        return False

    if exposure() > engine.capital * MAX_EXPOSURE:
        log("SKIP_EXPOSURE")
        return False

    f = await features(t)
    if not f:
        log(f"SKIP_FEATURES {m[:6]}")
        return False

    ok = True
    try:
        ok, _ = adaptive_filter(f, None, engine.no_trade_cycles)
    except Exception as e:
        log(f"FILTER_ERR {str(e)[:80]}")
        ok = True

    # 保留舊功能：冷啟動時不被 filter 卡死
    if not ok and engine.no_trade_cycles < 10:
        ok = True
        log(f"FILTER_BYPASS {m[:6]}")

    if not ok:
        log(f"SKIP_FILTER {m[:6]}")
        return False

    score = score_alpha(f)

    if score < MIN_SCORE:
        log(f"SKIP_SCORE {m[:6]} score={score:.3f}")
        return False

    s = size(score, src)
    if engine.capital < s:
        log("SKIP_CAPITAL")
        return False

    engine.capital -= s

    engine.positions.append(
        {
            "mint": m,
            "entry": f["price"],
            "size": s,
            "time": now,
            "peak": 0.0,
            "source": src,
        }
    )

    LAST_TRADE[m] = now
    LAST_GLOBAL_TRADE_TS = now

    engine.stats["signals"] += 1
    engine.stats["executed"] += 1
    engine.last_signal = f"{m[:6]} score={score:.3f} src={src}"

    log(f"BUY {m[:6]} score={score:.3f} src={src}")
    return True


# ================= LOOP =================
async def main_loop():
    ensure_engine()
    log("🔥 V34.1 FULL FUSION FUND MODE START")

    while engine.running:
        try:
            if not risk():
                break

            if not market_ok():
                engine.no_trade_cycles += 1
                log("MARKET_COOLDOWN")
                await asyncio.sleep(3)
                continue

            tokens = await fetch_candidates()

            if not tokens:
                engine.no_trade_cycles += 1
                log("NO_TOKENS")
                await asyncio.sleep(5)
                continue

            traded = False

            for t in tokens:
                did = await trade(t)
                traded = traded or did

            for p in list(engine.positions):
                await check_sell(p)

            if traded:
                engine.no_trade_cycles = 0
            else:
                engine.no_trade_cycles += 1

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"ERR {e}")

        await asyncio.sleep(2)
