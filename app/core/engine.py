import asyncio
import time

from app.core.state import engine
from app.core.scanner import scan
from app.core.pricing import get_price
from app.core.risk import allow
from app.core.risk_runtime import risk_engine

from app.alpha.breakout import breakout_score
from app.alpha.smart_money import smart_money_score
from app.alpha.liquidity import liquidity_score
from app.alpha.regime import detect_regime
from app.alpha.combiner import combine_scores, get_dynamic_weights
from app.alpha.signal_router import router
from app.alpha.entry_filter import should_enter
from app.alpha.wallet_tracker import record_wallet_trade
from app.alpha.helius_wallet_tracker import update_token_wallets

from app.portfolio.allocator import get_position_size
from app.portfolio.portfolio_manager import portfolio
from app.core.position_manager import manage_position


# ================= CONFIG =================
TP = 0.045
SL = -0.008
TRAIL = 0.004

TRADE_INTERVAL = 12


# ================= STATE =================
last_trade_time = 0
recent_changes = []


# ================= HELPERS =================
def calc_drawdown():
    if engine.peak_capital <= 0:
        return 0.0
    return (engine.capital - engine.peak_capital) / engine.peak_capital


def portfolio_can_add_more():
    return portfolio.can_add_more(engine, max_exposure=0.75)


def build_source_stats(history):
    stats = {}

    for t in history:
        src = t.get("meta", {}).get("source", "unknown")
        pnl = float(t.get("pnl", 0))

        if src not in stats:
            stats[src] = {"count": 0, "wins": 0, "pnl": 0}

        stats[src]["count"] += 1
        stats[src]["pnl"] += pnl

        if pnl >= 0:
            stats[src]["wins"] += 1

    for s in stats:
        c = stats[s]["count"]
        stats[s]["win_rate"] = stats[s]["wins"] / max(c, 1)
        stats[s]["avg_pnl"] = stats[s]["pnl"] / max(c, 1)

    return stats


def record_trade(pos, price, reason):
    pnl = (price - pos["entry"]) / pos["entry"]

    engine.trade_history.append({
        "mint": pos["mint"],
        "entry": pos["entry"],
        "exit": price,
        "pnl": pnl,
        "size": pos["size"],
        "reason": reason,
        "meta": pos.get("meta", {}),
    })

    if pnl >= 0:
        engine.stats["wins"] = engine.stats.get("wins", 0) + 1
    else:
        engine.stats["losses"] = engine.stats.get("losses", 0) + 1

    risk_engine.record_realized(pnl)

    return pnl


# ================= TRADING =================
def buy(mint, price, score, size, meta):
    global last_trade_time

    engine.capital -= size
    now = time.time()

    engine.positions.append({
        "mint": mint,
        "entry": price,
        "peak": price,
        "size": size,
        "time": now,
        "score": score,
        "meta": meta,
        "breakeven_armed": False,
        "stop_price": None,
        "tp1_done": False,
        "add_done": False,
    })

    record_wallet_trade("SIM_WALLET", mint, "buy", size)

    engine.stats["executed"] += 1
    last_trade_time = now
    risk_engine.record_trade()

    engine.log(f"BUY {mint[:6]} size={size:.4f} cap={engine.capital:.4f}")


def sell(pos, price, reason):
    pnl = record_trade(pos, price, reason)
    engine.capital += pos["size"] * (1 + pnl)
    engine.log(f"SELL {pos['mint'][:6]} {reason} pnl={pnl:.4f}")


def partial_sell(pos, price, ratio):
    sell_size = pos["size"] * ratio
    pnl = (price - pos["entry"]) / pos["entry"]

    engine.capital += sell_size * (1 + pnl)
    pos["size"] -= sell_size

    record_trade(pos, price, "PARTIAL")
    engine.log(f"PARTIAL {pos['mint'][:6]} ratio={ratio}")


def add_winner(pos, price, ratio):
    add_size = pos["size"] * ratio * 0.5

    if engine.capital < add_size:
        return

    engine.capital -= add_size

    old_size = pos["size"]
    new_size = old_size + add_size

    pos["entry"] = (pos["entry"] * old_size + price * add_size) / new_size
    pos["size"] = new_size

    record_wallet_trade("SIM_WALLET", pos["mint"], "buy", add_size)

    engine.log(f"ADD {pos['mint'][:6]} size={add_size:.4f}")


# ================= POSITION MANAGEMENT =================
async def manage_positions():
    now = time.time()
    remaining = []

    for pos in engine.positions:
        try:
            price = await get_price(pos["mint"])
            if not price:
                remaining.append(pos)
                continue

            if price > pos["peak"]:
                pos["peak"] = price

            pos["time_age"] = now - pos["time"]

            # 🔥 Position Manager
            actions = manage_position(pos, price)

            sold = False
            for act, ratio in actions:

                if act == "partial_sell":
                    partial_sell(pos, price, ratio)

                elif act == "add":
                    add_winner(pos, price, ratio)

                elif act == "sell_all":
                    sell(pos, price, "PM_EXIT")
                    sold = True
                    break

                elif act == "breakeven":
                    engine.log(
                        f"BREAKEVEN {pos['mint'][:6]} stop={pos.get('stop_price', pos['entry']):.4f}"
                    )

            if sold:
                continue

            pnl = (price - pos["entry"]) / pos["entry"]
            dd = (price - pos["peak"]) / pos["peak"]

            # fallback exits
            if pnl >= TP:
                sell(pos, price, "TP")
                continue

            if pnl <= SL:
                sell(pos, price, "SL")
                continue

            if pnl > 0.015 and dd < -TRAIL:
                sell(pos, price, "TRAIL")
                continue

            remaining.append(pos)

        except Exception as e:
            engine.log(f"ERR {e}")
            remaining.append(pos)

    engine.positions = remaining


# ================= ROUTE =================
async def evaluate_route(route):
    global last_trade_time

    token = route["token"]
    mint = route["mint"]
    source = route["source"]

    now = time.time()

    if now - last_trade_time < TRADE_INTERVAL:
        return

    # 🔥 更新鏈上 wallet
    asyncio.create_task(update_token_wallets(mint))

    b = breakout_score(token)
    s = smart_money_score(token)
    l = liquidity_score(token)

    stats = build_source_stats(engine.trade_history)
    weights = get_dynamic_weights(stats)

    score = combine_scores(b, s, l, engine.regime, stats)

    if score < 0.55:
        return

    price = await get_price(token)
    if not price:
        return

    base = get_position_size(score, engine.capital, engine)
    cap = portfolio.weighted_position_size(engine, source)

    size = min(base, cap)

    if not allow(engine, score, size):
        return

    ok, _ = should_enter(token, {"momentum": 0.01, "smart_money": s})
    if not ok:
        return

    buy(
        mint,
        price,
        score,
        size,
        {
            "source": source,
            "breakout": b,
            "smart_money": s,
            "liquidity": l,
            "weights": weights,
        },
    )


# ================= MAIN LOOP =================
async def main_loop():
    engine.log("🚀 ENGINE START")

    while engine.running:
        try:
            await manage_positions()

            tokens = await scan()

            for t in tokens:
                recent_changes.append(float(t.get("change", 0)))

            engine.regime = detect_regime(recent_changes[-40:])

            if engine.regime in ["flat", "trend_down"]:
                await asyncio.sleep(2)
                continue

            routes = router.build_routes(tokens)

            for r in routes:
                await evaluate_route(r)

        except Exception as e:
            engine.log(f"LOOP ERR {e}")

        await asyncio.sleep(2)
