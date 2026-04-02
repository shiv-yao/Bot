import asyncio
import time
from collections import defaultdict

from app.state import engine
from app.metrics import compute_metrics
from app.alpha.combiner import combine_scores
from app.alpha.adaptive_filter import adaptive_filter

# ===== SAFE IMPORT（相容你現在 repo）=====
try:
    from app.sources.pump import fetch_pump_candidates
except Exception:
    async def fetch_pump_candidates():
        return []

try:
    from app.data.market import get_quote
except Exception:
    async def get_quote(input_mint, output_mint, amount):
        return None

try:
    from app.alpha.helius_wallet_tracker import update_token_wallets
except Exception:
    async def update_token_wallets(mint):
        return []


# ===== CONFIG =====
MAX_POSITIONS = 4
MAX_EXPOSURE = 0.50
MAX_POSITION_SIZE = 0.20

TAKE_PROFIT = 0.04
STOP_LOSS = -0.02
TRAILING_GAP = 0.015
MAX_HOLD_SEC = 40

TOKEN_COOLDOWN = 10
FORCE_TRADE_INTERVAL = 15

SOL_MINT = "So11111111111111111111111111111111111111112"
QUOTE_AMOUNT = 1_000_000  # 0.001 SOL

LAST_TRADE = defaultdict(float)
LAST_EXECUTION = 0.0
LAST_MARK_PRICE = {}


# ===== INIT =====
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
    engine.regime = getattr(engine, "regime", "unknown")
    engine.last_signal = getattr(engine, "last_signal", "")
    engine.last_trade = getattr(engine, "last_trade", "")
    engine.no_trade_cycles = getattr(engine, "no_trade_cycles", 0)


# ===== LOG =====
def log(msg):
    print(msg)
    engine.logs.append(str(msg))
    engine.logs = engine.logs[-250:]


# ===== UTILS =====
def safe_float(x, default=0.0):
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def risk_check():
    if engine.peak_capital > 0:
        dd = (engine.capital - engine.peak_capital) / engine.peak_capital
    else:
        dd = 0.0

    if dd < -0.30:
        log("🛑 HARD STOP DD")
        engine.running = False
        return False

    return True


def exposure():
    return sum(safe_float(p.get("size", 0.0), 0.0) for p in engine.positions)


def wallet_alpha(wallets):
    if not wallets:
        return 0.0
    return min(len(wallets) / 8.0, 1.0)


# ===== 真價格 / 真特徵 =====
async def get_price(mint: str):
    q = await get_quote(SOL_MINT, mint, QUOTE_AMOUNT)
    if not q or not q.get("outAmount"):
        return None

    out_amount = safe_float(q.get("outAmount", 0.0), 0.0)
    if out_amount <= 0:
        return None

    # 用 SOL->token quote 的 outAmount 當可交易近似價格尺度
    return out_amount / 1e6, q


async def build_features(mint):
    try:
        wallets = await update_token_wallets(mint)
    except Exception:
        wallets = []

    price_data = await get_price(mint)
    if not price_data:
        return None

    price, q = price_data

    out_amount = safe_float(q.get("outAmount", 0.0), 0.0)
    price_impact = safe_float(q.get("priceImpactPct", 1.0), 1.0)

    if out_amount <= 0:
        return None

    prev = LAST_MARK_PRICE.get(mint)
    breakout = 0.02
    if prev and prev > 0:
        breakout = max((price - prev) / prev, 0.0)

    LAST_MARK_PRICE[mint] = price

    # 這裡沿用你目前系統實測後比較容易出手的尺度
    liquidity = out_amount / 1e5

    return {
        "breakout": breakout,
        "smart_money": wallet_alpha(wallets),
        "liquidity": liquidity,
        "insider": 0.05,
        "wallets": wallets,
        "wallet_count": len(wallets),
        "price_impact": price_impact,
        "price": price,
        "out_amount": out_amount,
    }


# ===== SIZE =====
def get_size(score):
    base = engine.capital * 0.08

    if score > 0.60:
        base *= 1.5
    elif score > 0.40:
        base *= 1.2

    if engine.peak_capital > 0:
        dd = (engine.capital - engine.peak_capital) / engine.peak_capital
        if dd < -0.10:
            base *= 0.6
        if dd < -0.20:
            base *= 0.4

    base = min(base, engine.capital * MAX_POSITION_SIZE)
    return max(base, 0.02)


# ===== PnL / trailing =====
async def mark_to_market(pos):
    price_data = await get_price(pos["mint"])
    if not price_data:
        return None, None

    price, _ = price_data
    entry = safe_float(pos.get("entry", 0.0), 0.0)
    if entry <= 0:
        return None, None

    pnl = (price - entry) / entry
    return pnl, price


def update_trailing(pos, pnl):
    peak = safe_float(pos.get("peak_pnl", pnl), pnl)
    if pnl > peak:
        pos["peak_pnl"] = pnl
        peak = pnl

    trail_line = peak - TRAILING_GAP
    return pnl < trail_line


# ===== SELL =====
async def try_sell(pos):
    pnl, price = await mark_to_market(pos)
    if pnl is None:
        return

    held = time.time() - pos["time"]

    reason = None
    if pnl >= TAKE_PROFIT:
        reason = "TP"
    elif pnl <= STOP_LOSS:
        reason = "SL"
    elif update_trailing(pos, pnl):
        reason = "TRAIL"
    elif held > MAX_HOLD_SEC:
        reason = "TIME"

    log(f"CHECK_EXIT {pos['mint'][:6]} pnl={pnl:.4f} held={held:.1f}")

    if not reason:
        return

    engine.positions.remove(pos)
    engine.capital += pos["size"] * (1 + pnl)

    engine.trade_history.append({
        "mint": pos["mint"],
        "pnl": pnl,
        "reason": reason,
        "score": pos.get("score", 0.0),
        "size": pos.get("size", 0.0),
        "timestamp": time.time(),
        "meta": pos.get("meta", {}),
    })

    if pnl >= 0:
        engine.stats["wins"] += 1
    else:
        engine.stats["losses"] += 1

    if engine.capital > engine.peak_capital:
        engine.peak_capital = engine.capital

    engine.last_trade = f"{pos['mint'][:6]} {reason} pnl={pnl:.4f}"
    log(f"SELL {pos['mint'][:6]} {reason} pnl={pnl:.4f}")


# ===== TRADE =====
async def try_trade(mint):
    global LAST_EXECUTION

    now = time.time()

    if now - LAST_TRADE[mint] < TOKEN_COOLDOWN:
        return False

    if any(p.get("mint") == mint for p in engine.positions):
        return False

    if len(engine.positions) >= MAX_POSITIONS:
        engine.stats["rejected"] += 1
        log("MAX_POSITIONS")
        return False

    if exposure() > engine.capital * MAX_EXPOSURE:
        engine.stats["rejected"] += 1
        log("EXPOSURE_LIMIT")
        return False

    f = await build_features(mint)
    if not f:
        engine.stats["rejected"] += 1
        log(f"NO_FEATURE {mint[:6]}")
        return False

    metrics = None
    if len(engine.trade_history) > 5:
        try:
            metrics = compute_metrics(engine)
        except Exception as e:
            log(f"METRICS_ERR {e}")
            metrics = None

    try:
        ok, th = adaptive_filter(
            f,
            metrics,
            no_trade_cycles=getattr(engine, "no_trade_cycles", 0),
        )
    except Exception as e:
        engine.stats["errors"] += 1
        log(f"FILTER_ERR {e}")
        return False

    state = th.get("state", "neutral")

    loosen = False
    if len(engine.trade_history) < 5:
        loosen = True
    if now - LAST_EXECUTION > FORCE_TRADE_INTERVAL:
        loosen = True

    if not ok:
        # V24.2 fallback：避免 0 交易
        if f["wallet_count"] >= 1 and f["liquidity"] > 0.0003:
            log(
                f"FORCE_PASS {mint[:6]} "
                f"state={state} "
                f"w={f['wallet_count']} "
                f"liq={f['liquidity']:.4f} "
                f"imp={f['price_impact']:.4f}"
            )
            loosen = True
        else:
            engine.stats["rejected"] += 1
            log(
                f"SKIP {mint[:6]} "
                f"state={state} "
                f"w={f['wallet_count']} "
                f"liq={f['liquidity']:.4f} "
                f"imp={f['price_impact']:.4f}"
            )
            return False

    score = combine_scores(
        f["breakout"],
        f["smart_money"],
        f["liquidity"],
        f["insider"],
        getattr(engine, "regime", "unknown"),
        {},
        {},
    )

    score *= th.get("score_boost", 1.0)

    if f["wallet_count"] <= 2:
        score *= 0.75

    min_score = th.get("score_min", 0.20)
    if loosen:
        min_score *= 0.8

    if score < min_score and not loosen:
        engine.stats["rejected"] += 1
        log(f"LOW_SCORE {mint[:6]} state={state} score={score:.4f}")
        return False

    size = get_size(score)
    if engine.capital < size:
        engine.stats["rejected"] += 1
        log("NO_CAPITAL")
        return False

    engine.capital -= size

    engine.positions.append({
        "mint": mint,
        "entry": f["price"],  # V25 真 entry 價格
        "size": size,
        "score": score,
        "time": now,
        "peak_pnl": 0.0,
        "meta": {
            **f,
            "source": "fusion",
            "filter_state": state,
            "forced": loosen,
        },
    })

    LAST_TRADE[mint] = now
    LAST_EXECUTION = now
    engine.stats["signals"] += 1
    engine.stats["executed"] += 1
    engine.last_signal = f"{mint[:6]} state={state} score={score:.4f}"

    if loosen and score < min_score:
        log(f"FORCE_BUY {mint[:6]} state={state} size={size:.4f} score={score:.3f}")
    else:
        log(f"BUY {mint[:6]} state={state} size={size:.4f} score={score:.3f}")

    return True


# ===== MAIN LOOP =====
async def main_loop():
    ensure_engine()
    log("🔥 V24.2 + V25 ENGINE START")

    while engine.running:
        traded_this_cycle = False

        try:
            if not risk_check():
                break

            tokens = await fetch_pump_candidates()

            for t in tokens:
                mint = t.get("mint")
                if mint:
                    did_trade = await try_trade(mint)
                    traded_this_cycle = traded_this_cycle or did_trade

            for pos in list(engine.positions):
                await try_sell(pos)

            if traded_this_cycle:
                engine.no_trade_cycles = 0
            else:
                engine.no_trade_cycles += 1
                log(f"NO_TRADE_CYCLE {engine.no_trade_cycles}")

            if len(engine.trade_history) > 5:
                try:
                    m = compute_metrics(engine)
                    log(
                        f"📊 WR={m['performance']['win_rate']} "
                        f"PF={m['performance']['profit_factor']}"
                    )
                except Exception as e:
                    log(f"METRICS_LOOP_ERR {e}")

        except Exception as e:
            engine.stats["errors"] += 1
            log(f"ERR {e}")

        await asyncio.sleep(2)
