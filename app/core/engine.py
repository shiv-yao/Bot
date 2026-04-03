import asyncio
import time
import random
from collections import defaultdict

from app.state import engine
from app.alpha.combiner import combine_scores
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


MAX_POSITIONS = 3
MAX_EXPOSURE = 0.45
MAX_POSITION_SIZE = 0.18

TAKE_PROFIT = 0.05
STOP_LOSS = -0.02
TRAILING_GAP = 0.012
MAX_HOLD_SEC = 45

TOKEN_COOLDOWN = 12

SOL = "So11111111111111111111111111111111111111112"
AMOUNT = 1_000_000

LAST_TRADE = defaultdict(float)
LAST_PRICE = {}


def ensure_engine():
    engine.positions = getattr(engine, "positions", [])
    engine.trade_history = getattr(engine, "trade_history", [])
    engine.logs = getattr(engine, "logs", [])

    engine.stats = getattr(engine, "stats", {
        "signals": 0,
        "executed": 0,
        "wins": 0,
        "losses": 0,
        "errors": 0,
        "rejected": 0,
    })

    engine.capital = getattr(engine, "capital", 5.0)
    engine.start_capital = getattr(engine, "start_capital", engine.capital)
    engine.peak_capital = getattr(engine, "peak_capital", engine.capital)

    engine.running = getattr(engine, "running", True)
    engine.no_trade_cycles = getattr(engine, "no_trade_cycles", 0)
    engine.last_signal = getattr(engine, "last_signal", "")
    engine.last_trade = getattr(engine, "last_trade", "")


def log(msg):
    print(msg)
    engine.logs.append(str(msg))
    engine.logs = engine.logs[-200:]


def sf(x):
    try:
        return float(x)
    except Exception:
        return 0.0


def risk():
    if engine.peak_capital > 0:
        dd = (engine.capital - engine.peak_capital) / engine.peak_capital
        if dd < -0.30:
            log("HARD STOP")
            engine.running = False
            return False
    return True


def exposure():
    return sum(sf(p.get("size", 0)) for p in engine.positions)


async def safe_quote(input_mint, output_mint, amount):
    for _ in range(3):
        try:
            q = await get_quote(input_mint, output_mint, amount)
            if q and q.get("outAmount"):
                return q
        except Exception as e:
            log(f"QUOTE_ERR {str(e)[:60]}")
        await asyncio.sleep(0.25 + random.random() * 0.35)
    return None


async def get_price(mint):
    if not looks_like_solana_mint(mint):
        return None

    q = await safe_quote(SOL, mint, AMOUNT)
    if not q:
        return None

    out = sf(q.get("outAmount", 0))
    if out <= 0:
        return None

    return out / 1e6, q


async def features(t):
    mint = t["mint"]
    source = t.get("source", "unknown")

    if not looks_like_solana_mint(mint):
        return None

    try:
        wallets = await update_token_wallets(mint)
    except Exception:
        wallets = []

    data = await get_price(mint)
    if not data:
        return None

    price, q = data

    prev = LAST_PRICE.get(mint)
    breakout = 0.0
    if prev and prev > 0:
        breakout = max((price - prev) / prev, 0.0)

    LAST_PRICE[mint] = price

    liq = sf(q.get("outAmount", 0)) / 1e5

    source_bonus = {
        "pump": 1.20,
        "dex": 1.00,
        "dex_boost": 1.10,
        "helius": 1.05,
        "jup": 0.85,
        "unknown": 1.00,
    }.get(source, 1.0)

    breakout *= source_bonus

    # 第一輪沒有 prev 時，不要直接過濾掉
    if prev is not None and breakout < 0.01:
        return None

    if liq < 0.002:
        return None

    # 保留你原本 smart money 邏輯，但不要過嚴
    if len(wallets) < 1:
        return None

    return {
        "mint": mint,
        "source": source,
        "breakout": breakout,
        "smart_money": min(len(wallets) / 8, 1.0),
        "liquidity": liq,
        "insider": 0.05,
        "wallet_count": len(wallets),
        "price": price,
    }


def size(score):
    base = engine.capital * 0.08
    if score > 0.4:
        base *= 1.3
    return min(base, engine.capital * MAX_POSITION_SIZE)


async def check_sell(p):
    data = await get_price(p["mint"])
    if not data:
        return

    price, _ = data
    entry = sf(p.get("entry", 0))
    if entry <= 0:
        return

    pnl = (price - entry) / entry
    held = time.time() - p["time"]

    reason = None
    if pnl >= TAKE_PROFIT:
        reason = "TP"
    elif pnl <= STOP_LOSS:
        reason = "SL"
    elif pnl < p.get("peak", 0.0) - TRAILING_GAP:
        reason = "TRAIL"
    elif held > MAX_HOLD_SEC and pnl < 0.01:
        reason = "TIME"

    if pnl > p.get("peak", 0.0):
        p["peak"] = pnl

    if not reason:
        return

    if p in engine.positions:
        engine.positions.remove(p)

    engine.capital += p["size"] * (1 + pnl)

    engine.trade_history.append({
        "mint": p["mint"],
        "pnl": pnl,
        "reason": reason,
        "timestamp": time.time(),
        "meta": {"source": p.get("source")},
    })
    engine.trade_history = engine.trade_history[-500:]

    if pnl > 0:
        engine.stats["wins"] += 1
    else:
        engine.stats["losses"] += 1

    if engine.capital > engine.peak_capital:
        engine.peak_capital = engine.capital

    engine.last_trade = f"{p['mint'][:6]} {reason} pnl={pnl:.4f}"
    log(f"SELL {p['mint'][:6]} {reason} pnl={pnl:.4f}")


async def trade(t):
    mint = t["mint"]

    if not looks_like_solana_mint(mint):
        log(f"BAD_MINT {mint}")
        return False

    if any(p["mint"] == mint for p in engine.positions):
        return False

    now = time.time()

    if now - LAST_TRADE[mint] < TOKEN_COOLDOWN:
        return False

    if len(engine.positions) >= MAX_POSITIONS:
        return False

    if exposure() > engine.capital * MAX_EXPOSURE:
        return False

    f = await features(t)
    if not f:
        return False

    try:
        ok, _ = adaptive_filter(f, None, engine.no_trade_cycles)
    except Exception as e:
        engine.stats["errors"] += 1
        log(f"FILTER_ERR {str(e)[:60]}")
        return False

    if not ok:
        return False

    score = combine_scores(
        f["breakout"],
        f["smart_money"],
        f["liquidity"],
        f["insider"],
        "unknown",
        {},
        {},
    )

    min_score = 0.22 if f["source"] == "pump" else 0.27
    if score < min_score:
        return False

    s = size(score)
    if engine.capital < s:
        return False

    engine.capital -= s

    engine.positions.append({
        "mint": mint,
        "entry": f["price"],
        "size": s,
        "time": now,
        "peak": 0.0,
        "source": f["source"],
    })

    LAST_TRADE[mint] = now
    engine.stats["signals"] += 1
    engine.stats["executed"] += 1
    engine.last_signal = f"{mint[:6]} src={f['source']} score={score:.3f}"

    log(f"BUY {mint[:6]} src={f['source']} score={score:.3f}")
    return True


async def main_loop():
    ensure_engine()
    log("V29.2 HARDENED START")

    while engine.running:
        traded = False

        try:
            if not risk():
                break

            tokens = await fetch_candidates()

            if not tokens:
                engine.no_trade_cycles += 1
                log("NO_TOKENS_FROM_MARKET")
                await asyncio.sleep(5)
                continue

            for t in tokens:
                if await trade(t):
                    traded = True

            for p in list(engine.positions):
                await check_sell(p)

            if traded:
                engine.no_trade_cycles = 0
            else:
                engine.no_trade_cycles += 1

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"ERR {str(e)[:120]}")

        await asyncio.sleep(2)
