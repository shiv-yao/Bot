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
from app.alpha.insider_engine import get_token_insider_score

from app.portfolio.allocator import get_position_size
from app.portfolio.portfolio_manager import portfolio
from app.core.position_manager import manage_position

TP = 0.045
SL = -0.008
TRAIL = 0.004
TRADE_INTERVAL = 12

last_trade_time = 0
recent_changes = []


def calc_drawdown() -> float:
    if engine.peak_capital <= 0:
        return 0.0
    return (engine.capital - engine.peak_capital) / engine.peak_capital


def portfolio_can_add_more() -> bool:
    return portfolio.can_add_more(engine, max_exposure=0.75)


def build_source_stats(history: list[dict]) -> dict:
    stats = {}

    for t in history:
        src = t.get("meta", {}).get("source", "unknown")
        pnl = float(t.get("pnl", 0.0) or 0.0)

        if src not in stats:
            stats[src] = {
                "count": 0,
                "wins": 0,
                "losses": 0,
                "pnl": 0.0,
                "avg_pnl": 0.0,
                "win_rate": 0.0,
            }

        stats[src]["count"] += 1
        stats[src]["pnl"] += pnl

        if pnl >= 0:
            stats[src]["wins"] += 1
        else:
            stats[src]["losses"] += 1

    for src, row in stats.items():
        count = max(int(row["count"]), 1)
        row["win_rate"] = row["wins"] / count
        row["avg_pnl"] = row["pnl"] / count

    return stats


def build_insider_perf(history: list[dict], threshold: float = 0.30) -> dict:
    buckets = {
        "high_insider": {
            "count": 0,
            "wins": 0,
            "losses": 0,
            "total_pnl": 0.0,
            "avg_pnl": 0.0,
            "win_rate": 0.0,
        },
        "low_insider": {
            "count": 0,
            "wins": 0,
            "losses": 0,
            "total_pnl": 0.0,
            "avg_pnl": 0.0,
            "win_rate": 0.0,
        },
    }

    for t in history:
        meta = t.get("meta", {}) or {}
        pnl = float(t.get("pnl", 0.0) or 0.0)
        insider = float(meta.get("insider", 0.0) or 0.0)

        name = "high_insider" if insider >= threshold else "low_insider"
        row = buckets[name]

        row["count"] += 1
        row["total_pnl"] += pnl

        if pnl >= 0:
            row["wins"] += 1
        else:
            row["losses"] += 1

    for row in buckets.values():
        count = max(row["count"], 1)
        row["avg_pnl"] = row["total_pnl"] / count
        row["win_rate"] = row["wins"] / count

    return {
        "high_insider": buckets["high_insider"],
        "low_insider": buckets["low_insider"],
        "comparison": {
            "avg_pnl_diff": buckets["high_insider"]["avg_pnl"] - buckets["low_insider"]["avg_pnl"],
            "win_rate_diff": buckets["high_insider"]["win_rate"] - buckets["low_insider"]["win_rate"],
            "threshold": threshold,
        },
    }


def record_trade(pos: dict, price: float, reason: str) -> float:
    pnl = (price - pos["entry"]) / pos["entry"]

    engine.trade_history.append({
        "mint": pos["mint"],
        "entry": pos["entry"],
        "exit": price,
        "pnl": pnl,
        "size": pos["size"],
        "reason": reason,
        "score": pos.get("score"),
        "meta": pos.get("meta", {}),
    })

    if pnl >= 0:
        engine.stats["wins"] = engine.stats.get("wins", 0) + 1
    else:
        engine.stats["losses"] = engine.stats.get("losses", 0) + 1

    risk_engine.record_realized(pnl)
    return pnl


def buy(mint: str, price: float, score: float, size: float, meta: dict):
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

    engine.stats["executed"] = engine.stats.get("executed", 0) + 1
    last_trade_time = now
    risk_engine.record_trade()

    weights = meta.get("weights", {})

    engine.log(
        "BUY "
        f"{mint[:6]} "
        f"size={size:.4f} "
        f"score={score:.4f} "
        f"src={meta.get('source', 'unknown')} "
        f"b={meta.get('breakout', 0):.3f} "
        f"s={meta.get('smart_money', 0):.3f} "
        f"l={meta.get('liquidity', 0):.3f} "
        f"ins={meta.get('insider', 0):.3f} "
        f"wb={weights.get('breakout', 0):.2f} "
        f"ws={weights.get('smart_money', 0):.2f} "
        f"wl={weights.get('liquidity', 0):.2f} "
        f"wi={weights.get('insider', 0):.2f} "
        f"cap={engine.capital:.4f}"
    )


def sell(pos: dict, price: float, reason: str):
    pnl = record_trade(pos, price, reason)
    engine.capital += pos["size"] * (1 + pnl)
    engine.log(
        f"SELL {pos['mint'][:6]} {reason} pnl={pnl:.4f} "
        f"src={pos.get('meta', {}).get('source', 'unknown')} "
        f"ins={pos.get('meta', {}).get('insider', 0):.3f}"
    )


def partial_sell(pos: dict, price: float, ratio: float):
    sell_size = pos["size"] * ratio
    pnl = (price - pos["entry"]) / pos["entry"]

    engine.capital += sell_size * (1 + pnl)
    pos["size"] -= sell_size

    engine.trade_history.append({
        "mint": pos["mint"],
        "entry": pos["entry"],
        "exit": price,
        "pnl": pnl,
        "size": sell_size,
        "reason": "PARTIAL",
        "score": pos.get("score"),
        "meta": pos.get("meta", {}),
    })

    risk_engine.record_realized(pnl)
    engine.log(
        f"PARTIAL {pos['mint'][:6]} ratio={ratio} pnl={pnl:.4f} "
        f"ins={pos.get('meta', {}).get('insider', 0):.3f}"
    )


def add_winner(pos: dict, price: float, ratio: float):
    add_size = pos["size"] * ratio * 0.5
    if engine.capital < add_size:
        return

    engine.capital -= add_size

    old_size = pos["size"]
    new_size = old_size + add_size

    pos["entry"] = (pos["entry"] * old_size + price * add_size) / new_size
    pos["size"] = new_size
    pos["peak"] = max(pos.get("peak", price), price)

    record_wallet_trade("SIM_WALLET", pos["mint"], "buy", add_size)

    engine.log(
        f"ADD {pos['mint'][:6]} size={add_size:.4f} "
        f"new_entry={pos['entry']:.4f} "
        f"ins={pos.get('meta', {}).get('insider', 0):.3f}"
    )


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
                        f"BREAKEVEN {pos['mint'][:6]} "
                        f"stop={pos.get('stop_price', pos['entry']):.4f} "
                        f"ins={pos.get('meta', {}).get('insider', 0):.3f}"
                    )

            if sold:
                continue

            pnl = (price - pos["entry"]) / pos["entry"]
            dd = (price - pos["peak"]) / pos["peak"]

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


async def evaluate_route(route: dict):
    global last_trade_time

    token = route["token"]
    mint = route["mint"]
    source = route["source"]
    now = time.time()

    if now - last_trade_time < TRADE_INTERVAL:
        return

    asyncio.create_task(update_token_wallets(mint))

    b = breakout_score(token)
    s = smart_money_score(token)
    l = liquidity_score(token)
    insider = get_token_insider_score(mint)

    source_stats = build_source_stats(engine.trade_history)
    insider_perf = build_insider_perf(engine.trade_history)
    weights = get_dynamic_weights(source_stats, insider_perf)

    score = combine_scores(
        breakout=b,
        smart_money=s,
        liquidity=l,
        insider=insider,
        regime=engine.regime,
        source_stats=source_stats,
        insider_perf=insider_perf,
    )

    engine.log(
        f"ROUTE {mint[:6]} "
        f"src={source} "
        f"route={float(route.get('score', 0.0) or 0.0):.3f} "
        f"final={score:.3f} "
        f"b={b:.3f} "
        f"s={s:.3f} "
        f"l={l:.3f} "
        f"ins={insider:.3f} "
        f"wb={weights['breakout']:.2f} "
        f"ws={weights['smart_money']:.2f} "
        f"wl={weights['liquidity']:.2f} "
        f"wi={weights['insider']:.2f} "
        f"regime={engine.regime}"
    )

    if score < 0.55:
        return

    price = await get_price(token)
    if not price:
        return

    base = get_position_size(score, engine.capital, engine)
    cap = portfolio.weighted_position_size(engine, source)

    # insider 高分可加一點倉，但仍受 portfolio cap 限制
    insider_boost = 1.0
    if insider >= 0.50:
        insider_boost = 1.20
    elif insider >= 0.30:
        insider_boost = 1.10

    size = min(base * insider_boost, cap)

    if not allow(engine, score, size):
        return

    ok, _ = should_enter(
        token,
        {
            "momentum": 0.01,
            "smart_money": s,
        }
    )
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
            "insider": insider,
            "weights": weights,
        },
    )


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
