# ================= V37.6 FULL FUSION TRUE EXECUTION =================

import os
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
    from app.alpha.helius_wallet_tracker import update_token_wallets
except Exception:
    async def update_token_wallets(mint):
        return []

try:
    from app.execution.jupiter_exec import execute_swap
except Exception:
    async def execute_swap(input_mint, output_mint, amount_atomic):
        return {"paper": True}

try:
    from app.data.market import get_quote
except Exception:
    async def get_quote(input_mint, output_mint, amount):
        return None


# ================= CONFIG =================

REAL_TRADING = os.getenv("REAL_TRADING", "false").lower() == "true"

SOL = "So11111111111111111111111111111111111111112"
SOL_DECIMALS = 1_000_000_000

AMOUNT = 1_000_000

MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "3"))
MAX_EXPOSURE = float(os.getenv("MAX_EXPOSURE", "0.50"))
MAX_POSITION_SIZE = float(os.getenv("MAX_POSITION_SIZE", "0.25"))

TAKE_PROFIT = float(os.getenv("TAKE_PROFIT", "0.05"))
STOP_LOSS = float(os.getenv("STOP_LOSS", "-0.02"))
TRAILING_GAP = float(os.getenv("TRAILING_GAP", "0.012"))
MAX_HOLD_SEC = int(os.getenv("MAX_HOLD_SEC", "60"))

TOKEN_COOLDOWN = int(os.getenv("TOKEN_COOLDOWN", "10"))
LOOP_SLEEP_SEC = float(os.getenv("LOOP_SLEEP_SEC", "2"))
FORCE_TRADE_AFTER = int(os.getenv("FORCE_TRADE_AFTER", "15"))

ENTRY_THRESHOLD = float(os.getenv("ENTRY_THRESHOLD", "0.003"))
SNIPER_FALLBACK_THRESHOLD = float(os.getenv("SNIPER_FALLBACK_THRESHOLD", "0.001"))

MIN_ORDER_SOL = float(os.getenv("MIN_ORDER_SOL", "0.01"))
MAX_FAKE_PNL_ABS = float(os.getenv("MAX_FAKE_PNL_ABS", "0.50"))


# ================= RUNTIME MEMORY =================

LAST_TRADE = defaultdict(float)
LAST_PRICE = {}
SOURCE_STATS = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": 0.0})


# ================= ENGINE INIT =================

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

    engine.stats = getattr(engine, "stats", {
        "signals": 0,
        "executed": 0,
        "rejected": 0,
        "errors": 0,
        "open_positions": 0,
        "open_exposure": 0.0,
        "trades": 0,
        "wins": 0,
        "losses": 0,
        "forced_trades": 0,
    })


# ================= LOG =================

def log(msg: str):
    print(msg)
    engine.logs.append(str(msg))
    engine.logs = engine.logs[-500:]


# ================= HELPERS =================

def sf(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default

def now_ts():
    return time.time()

def exposure():
    return sum(sf(p.get("size", 0.0)) for p in engine.positions)

def update_open_stats():
    engine.stats["open_positions"] = len(engine.positions)
    engine.stats["open_exposure"] = exposure()

def update_peak_capital():
    engine.peak_capital = max(sf(engine.peak_capital), sf(engine.capital))

def push_trade_history(row: dict):
    engine.trade_history.append(row)
    engine.trade_history = engine.trade_history[-1000:]
    engine.stats["trades"] = len(engine.trade_history)


# ================= PRICE =================

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

async def get_price(mint):
    q = await safe_quote(SOL, mint, AMOUNT)
    if not q:
        log(f"NO_QUOTE {mint[:6]}")
        return None

    out_amount = sf(q.get("outAmount", 0))
    if out_amount <= 0 or out_amount > 1e12:
        log(f"BAD_OUTAMOUNT {mint[:6]} {out_amount}")
        return None

    price = out_amount / 1e6
    if price <= 0 or price > 1000:
        log(f"BAD_PRICE {mint[:6]} {price}")
        return None

    return price


# ================= FEATURES =================

async def features(token_row):
    mint = token_row.get("mint")
    if not mint:
        return None

    source = token_row.get("source", "unknown")

    wallets = await update_token_wallets(mint)
    price = await get_price(mint)
    if price is None:
        return None

    prev = LAST_PRICE.get(mint)

    if prev:
        breakout = max((price - prev) / prev, 0.0)
    else:
        breakout = 0.005

    if breakout == 0:
        breakout = random.uniform(0.001, 0.003)

    LAST_PRICE[mint] = price

    smart_money = min(len(wallets) / 5.0, 1.0)

    liquidity = sf(token_row.get("liquidity", 0.0))
    if liquidity <= 0:
        liquidity = random.uniform(0.001, 0.01)

    return {
        "mint": mint,
        "source": source,
        "price": price,
        "breakout": breakout,
        "smart_money": smart_money,
        "liquidity": liquidity,
        "wallet_count": len(wallets),
        "is_new": prev is None,
    }


# ================= SOURCE MEMORY =================

def source_weight(src: str):
    s = SOURCE_STATS[src]
    total = s["wins"] + s["losses"]

    if total < 3:
        return 1.0

    winrate = s["wins"] / total
    if winrate > 0.6:
        return 1.2
    if winrate < 0.3:
        return 0.7
    return 1.0


# ================= MODE / SCORE =================

def detect_mode(f):
    if f["is_new"]:
        return "sniper"
    if f["smart_money"] > 0.6:
        return "smart"
    return "momentum"

def score_alpha(f):
    mode = detect_mode(f)

    if mode == "sniper":
        base = (
            f["breakout"] * 0.40 +
            f["liquidity"] * 0.30 +
            f["smart_money"] * 0.30
        )
    elif mode == "smart":
        base = (
            f["smart_money"] * 0.50 +
            f["breakout"] * 0.30 +
            f["liquidity"] * 0.20
        )
    else:
        base = (
            f["breakout"] * 0.60 +
            f["liquidity"] * 0.30 +
            f["smart_money"] * 0.10
        )

    return base * source_weight(f["source"]), mode


# ================= POSITION SIZE =================

def size(score):
    base = engine.capital * 0.05

    if score > 0.01:
        base *= 1.5

    base = min(base, 0.20)
    return min(base, engine.capital * MAX_POSITION_SIZE)


# ================= BUY =================

async def buy_position(mint, f, mode, score, s, forced=False):
    order_sol = max(s, MIN_ORDER_SOL)
    order_amount_atomic = int(order_sol * SOL_DECIMALS)

    engine.stats["signals"] += 1

    log(
        f"TRY_BUY {mint[:6]} mode={mode} score={score:.4f} "
        f"size={s:.4f} order_sol={order_sol:.4f}"
    )

    res = await execute_swap(SOL, mint, order_amount_atomic)

    if not res:
        log(f"EXEC_EMPTY {mint[:6]}")
        engine.stats["errors"] += 1
        return False

    if res.get("error"):
        log(f"EXEC_FAIL {mint[:6]} {res.get('error')}")
        engine.stats["errors"] += 1
        return False

    is_paper = bool(res.get("paper"))
    tx_sig = None

    if isinstance(res.get("result"), str):
        tx_sig = res["result"]
    elif isinstance(res.get("signature"), str):
        tx_sig = res["signature"]

    quote_meta = res.get("quote", {}) if isinstance(res, dict) else {}
    token_amount_atomic = 0
    try:
        token_amount_atomic = int(quote_meta.get("outAmount") or 0)
    except Exception:
        token_amount_atomic = 0

    engine.capital -= s
    update_peak_capital()

    pos = {
        "mint": mint,
        "entry": f["price"],
        "size": s,
        "order_sol": order_sol,
        "amount_atomic": order_amount_atomic,
        "token_amount_atomic": token_amount_atomic,
        "time": now_ts(),
        "source": f["source"],
        "mode": mode,
        "score": score,
        "tx_buy": tx_sig,
        "paper": is_paper,
        "high_water": f["price"],
        "wallet_count": f.get("wallet_count", 0),
        "forced": forced,
    }

    engine.positions.append(pos)
    LAST_TRADE[mint] = now_ts()

    engine.stats["executed"] += 1
    if forced:
        engine.stats["forced_trades"] += 1

    update_open_stats()

    engine.last_signal = (
        f"BUY {mint[:6]} mode={mode} score={score:.4f} entry={f['price']:.8f}"
    )
    engine.last_trade = engine.last_signal

    log(
        f"BUY {mint[:6]} {mode} score={score:.4f} "
        f"entry={f['price']:.8f} "
        f"{'PAPER' if is_paper else f'tx={tx_sig}'}"
    )
    return True


# ================= TRADE =================

async def trade(token_row, forced=False):
    if exposure() > engine.capital * MAX_EXPOSURE:
        log("BLOCK_EXPOSURE")
        engine.stats["rejected"] += 1
        return False

    mint = token_row.get("mint")
    if not mint:
        log("SKIP_NO_MINT")
        return False

    if any(p.get("mint") == mint for p in engine.positions):
        log(f"SKIP_DUP_POS {mint[:6]}")
        return False

    if now_ts() - LAST_TRADE[mint] < TOKEN_COOLDOWN:
        log(f"SKIP_COOLDOWN {mint[:6]}")
        return False

    if len(engine.positions) >= MAX_POSITIONS:
        log("SKIP_MAX_POSITIONS")
        return False

    f = await features(token_row)
    if not f:
        engine.stats["rejected"] += 1
        return False

    ok, _ = adaptive_filter(f, None, engine.no_trade_cycles)
    if not ok:
        ok = engine.no_trade_cycles > 5 or forced

    if not ok:
        log(f"FILTER_BLOCK {mint[:6]}")
        engine.stats["rejected"] += 1
        return False

    score, mode = score_alpha(f)

    if score < ENTRY_THRESHOLD:
        if not ((mode == "sniper" and score > SNIPER_FALLBACK_THRESHOLD) or forced):
            log(f"SKIP_SCORE {mint[:6]} mode={mode} score={score:.4f}")
            engine.stats["rejected"] += 1
            return False

    s = size(score)
    if s <= 0:
        log(f"SKIP_SIZE {mint[:6]} size={s}")
        return False

    if engine.capital < s:
        log(f"SKIP_NO_CAPITAL {mint[:6]} capital={engine.capital:.4f} need={s:.4f}")
        return False

    return await buy_position(mint, f, mode, score, s, forced=forced)


# ================= SELL HELPERS =================

def should_sell_reason(pos, price_now):
    mint = pos["mint"]
    entry = sf(pos.get("entry", 0))
    if entry <= 0:
        return None, None

    pos["high_water"] = max(sf(pos.get("high_water", entry)), price_now)

    pnl = (price_now - entry) / entry

    if abs(pnl) > MAX_FAKE_PNL_ABS:
        log(f"INVALID_PNL {mint[:6]} pnl={pnl:.4f}")
        return None, None

    age = now_ts() - sf(pos.get("time", now_ts()))
    trailing_trigger = (
        price_now <= pos["high_water"] * (1 - TRAILING_GAP)
        and pos["high_water"] > entry
    )

    reason = None
    if pnl >= TAKE_PROFIT:
        reason = "TP"
    elif pnl <= STOP_LOSS:
        reason = "SL"
    elif trailing_trigger:
        reason = "TRAIL"
    elif age >= MAX_HOLD_SEC:
        reason = "TIME"

    return reason, pnl

async def execute_sell_position(pos):
    mint = pos["mint"]

    if pos.get("paper"):
        return {"paper": True}

    token_amount_atomic = int(pos.get("token_amount_atomic") or 0)
    if token_amount_atomic <= 0:
        return {"error": "NO_TOKEN_AMOUNT"}

    return await execute_swap(mint, SOL, token_amount_atomic)


# ================= SELL =================

async def check_sell(pos):
    mint = pos["mint"]

    price_now = await get_price(mint)
    if price_now is None:
        return False

    reason, pnl = should_sell_reason(pos, price_now)
    if not reason:
        return False

    sell_res = await execute_sell_position(pos)

    if not sell_res:
        log(f"SELL_EMPTY {mint[:6]}")
        engine.stats["errors"] += 1
        return False

    if sell_res.get("error"):
        log(f"SELL_FAIL {mint[:6]} {sell_res.get('error')}")
        engine.stats["errors"] += 1
        return False

    is_paper = bool(sell_res.get("paper"))
    tx_sig = None

    if isinstance(sell_res.get("result"), str):
        tx_sig = sell_res["result"]
    elif isinstance(sell_res.get("signature"), str):
        tx_sig = sell_res["signature"]

    if pos in engine.positions:
        engine.positions.remove(pos)

    realized_capital = pos["size"] * (1 + pnl)
    engine.capital += realized_capital
    update_peak_capital()

    src = pos.get("source", "unknown")
    if pnl > 0:
        SOURCE_STATS[src]["wins"] += 1
        engine.stats["wins"] += 1
    else:
        SOURCE_STATS[src]["losses"] += 1
        engine.stats["losses"] += 1

    SOURCE_STATS[src]["pnl"] += pnl

    row = {
        "mint": mint,
        "entry": pos.get("entry"),
        "exit": price_now,
        "size": pos.get("size"),
        "pnl": pnl,
        "reason": reason,
        "mode": pos.get("mode"),
        "source": src,
        "time_open": pos.get("time"),
        "time_close": now_ts(),
        "paper": is_paper,
        "tx_buy": pos.get("tx_buy"),
        "tx_sell": tx_sig,
        "token_amount_atomic": pos.get("token_amount_atomic", 0),
    }
    push_trade_history(row)
    update_open_stats()

    engine.last_trade = (
        f"SELL {mint[:6]} reason={reason} pnl={pnl:.4f} exit={price_now:.8f}"
    )

    log(
        f"SELL {mint[:6]} {reason} pnl={pnl:.4f} "
        f"{'PAPER' if is_paper else f'tx={tx_sig}'}"
    )

    return True


# ================= METRICS =================

def get_metrics():
    wins = engine.stats.get("wins", 0)
    losses = engine.stats.get("losses", 0)
    trades = engine.stats.get("trades", 0)

    start_capital = sf(engine.start_capital, 5.0)
    capital = sf(engine.capital, start_capital)
    peak = max(sf(engine.peak_capital, capital), capital)

    total_return = capital - start_capital
    return_pct = (total_return / start_capital * 100.0) if start_capital > 0 else 0.0
    drawdown = ((peak - capital) / peak) if peak > 0 else 0.0

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
    log(f"🚀 V37.6 FULL FUSION START {'REAL' if REAL_TRADING else 'PAPER'}")

    while engine.running:
        try:
            tokens = await fetch_candidates()
            if not isinstance(tokens, list):
                tokens = []

            random.shuffle(tokens)
            traded = False

            for t in tokens[:20]:
                ok = await trade(t, forced=False)
                if ok:
                    traded = True

            for pos in list(engine.positions):
                await check_sell(pos)

            if not traded:
                engine.no_trade_cycles += 1
            else:
                engine.no_trade_cycles = 0

            if engine.no_trade_cycles > FORCE_TRADE_AFTER and tokens:
                log("FORCE_TRADE")
                await trade(tokens[0], forced=True)

            update_open_stats()
            update_peak_capital()

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"ERR {e}")

        await asyncio.sleep(LOOP_SLEEP_SEC)
