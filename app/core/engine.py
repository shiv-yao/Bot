import asyncio
import time

from app.core.state import engine
from app.core.scanner import scan
from app.core.pricing import get_price
from app.core.risk import allow
from app.core.risk_runtime import risk_engine
from app.core.position_manager import manage_position

from app.alpha.breakout import breakout_score
from app.alpha.smart_money import smart_money_score
from app.alpha.liquidity import liquidity_score
from app.alpha.regime import detect_regime
from app.alpha.combiner import get_dynamic_weights
from app.alpha.signal_router import router
from app.alpha.entry_filter import should_enter
from app.alpha.wallet_tracker import record_wallet_trade
from app.alpha.insider_engine import get_token_insider_score

# v7 / v8 wallet alpha 路線
from app.alpha.wallet_alpha_v7 import (
    get_wallet_alpha,
    record_token_wallets,
    record_wallet_trade as record_ranked_wallet_trade,
)
from app.alpha.helius_smart_wallet import fetch_smart_wallets

from app.portfolio.allocator import get_position_size
from app.portfolio.portfolio_manager import portfolio

TP = 0.045
SL = -0.008
TRAIL = 0.004
TRADE_INTERVAL = 12

last_trade_time = 0
recent_changes = []


def calc_drawdown() -> float:
    if getattr(engine, "peak_capital", 0) <= 0:
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

    wallet_for_trade = meta.get("wallet")
    if not wallet_for_trade:
        wallet_for_trade = "BOOTSTRAP_WALLET"

    meta = {**meta, "wallet": wallet_for_trade}

    engine.positions.append({
        "mint": mint,
        "entry": price,
        "peak": price,
        "size": size,
        "time": now,
        "score": score,
        "meta": meta,
        "wallet": wallet_for_trade,
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
        f"wallet={meta.get('wallet')} "
        f"top_wallet_count={meta.get('top_wallet_count', 0)} "
        f"wallet_alpha_avg={meta.get('wallet_alpha_avg', 0):.3f} "
        f"wallet_alpha_best={meta.get('wallet_alpha_best', 0):.3f} "
        f"wallet_cluster={meta.get('wallet_cluster', 0):.3f} "
        f"wallet_copy_signal={meta.get('wallet_copy_signal', 0)} "
        f"wb={weights.get('breakout', 0):.2f} "
        f"ws={weights.get('smart_money', 0):.2f} "
        f"wl={weights.get('liquidity', 0):.2f} "
        f"wi={weights.get('insider', 0):.2f} "
        f"cap={engine.capital:.4f}"
    )


def sell(pos: dict, price: float, reason: str):
    pnl = record_trade(pos, price, reason)

    wallet = pos.get("wallet") or (pos.get("meta", {}) or {}).get("wallet")
    if not wallet:
        wallet = "BOOTSTRAP_WALLET"

    record_ranked_wallet_trade(wallet, pnl)

    engine.capital += pos["size"] * (1 + pnl)
    engine.log(
        f"SELL {pos['mint'][:6]} {reason} pnl={pnl:.4f} "
        f"src={pos.get('meta', {}).get('source', 'unknown')} "
        f"ins={pos.get('meta', {}).get('insider', 0):.3f} "
        f"wallet={wallet}"
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

    wallet = pos.get("wallet") or (pos.get("meta", {}) or {}).get("wallet")
    if not wallet:
        wallet = "BOOTSTRAP_WALLET"

    record_ranked_wallet_trade(wallet, pnl)

    risk_engine.record_realized(pnl)
    engine.log(
        f"PARTIAL {pos['mint'][:6]} ratio={ratio} pnl={pnl:.4f} "
        f"ins={pos.get('meta', {}).get('insider', 0):.3f} "
        f"wallet={wallet}"
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

    mint = route["mint"]
    token = route["token"]
    source = route.get("source", "unknown")
    now = time.time()

    if now - last_trade_time < TRADE_INTERVAL:
        return

    # 1. 鏈上 smart wallets
    try:
        wallets = await fetch_smart_wallets(mint)
    except Exception as e:
        engine.log(f"WALLET_FETCH_ERR {mint[:6]} {e}")
        wallets = []

    if wallets:
        record_token_wallets(mint, wallets)
        engine.log(f"WALLET_OK {mint[:6]} {len(wallets)}")
    else:
        engine.log(f"WALLET_EMPTY {mint[:6]}")

    # 2. wallet alpha
    wa = get_wallet_alpha(mint)
    if not wa:
        engine.log(f"REJECT_NO_WALLET {mint[:6]}")
        return

    w_avg = float(wa["avg"])
    w_best = float(wa["best"])
    w_cluster = float(wa["cluster"])
    copy_signal = int(wa["copy_signal"])
    top_wallet = wa["top_wallet"]
    top_wallet_count = int(wa["count"])

    engine.log(
        f"WALLET_ALPHA {mint[:6]} "
        f"avg={w_avg:.3f} "
        f"best={w_best:.3f} "
        f"cluster={w_cluster:.3f} "
        f"copy={copy_signal}"
    )
    engine.log(f"LEAD_WALLET {mint[:6]} {top_wallet}")

    # 3. 原 alpha
    b = breakout_score(token)
    l = liquidity_score(token)
    s = await smart_money_score(mint)
    ins = get_token_insider_score(mint)

    engine.log(
        f"ALPHA_RAW {mint[:6]} "
        f"b={b:.3f} s={s:.3f} l={l:.3f} ins={ins:.3f}"
    )

    # 4. 權重分析
    source_stats = build_source_stats(engine.trade_history)
    insider_perf = build_insider_perf(engine.trade_history)
    weights = get_dynamic_weights(source_stats, insider_perf)

    # 5. v8 主融合 score
    score = (
        b * 0.25 +
        l * 0.10 +
        s * 0.20 +
        ins * 0.15 +
        w_avg * 0.30
    )
    score = round(min(score, 1.0), 4)

    engine.log(
        f"ROUTE {mint[:6]} "
        f"src={source} "
        f"score={score:.4f} "
        f"b={b:.3f} "
        f"s={s:.3f} "
        f"l={l:.3f} "
        f"ins={ins:.3f} "
        f"top_wallet_count={top_wallet_count} "
        f"wallet_alpha_avg={w_avg:.3f} "
        f"wallet_alpha_best={w_best:.3f} "
        f"wallet_cluster={w_cluster:.3f} "
        f"wallet_copy_signal={copy_signal} "
        f"wb={weights.get('breakout', 0):.2f} "
        f"ws={weights.get('smart_money', 0):.2f} "
        f"wl={weights.get('liquidity', 0):.2f} "
        f"wi={weights.get('insider', 0):.2f} "
        f"regime={engine.regime}"
    )

    # 6. 強制條件
    if w_best < 0.2:
        engine.log(f"REJECT_NO_SMART {mint[:6]}")
        return

    if w_cluster < 0.2:
        engine.log(f"REJECT_NO_CLUSTER {mint[:6]}")
        return

    entry_threshold = 0.30
    if engine.regime == "trend_up":
        entry_threshold = 0.26

    if score < entry_threshold:
        engine.log(f"REJECT_SCORE {mint[:6]} score={score:.3f} thr={entry_threshold:.3f}")
        return

    # 7. 價格
    price = await get_price(token)
    if not price:
        engine.log(f"NO_PRICE {mint[:6]}")
        return

    # 8. 倉位
    base = get_position_size(score, engine.capital, engine)
    cap = portfolio.weighted_position_size(engine, source)
    size = min(base, cap)

    if s <= 0.15:
        size *= 0.5
        engine.log(f"SIZE_CUT_LOW_SMART {mint[:6]} size={size:.4f}")

    if w_best > 0.6:
        size *= 2.0
        engine.log(f"SIZE_BEST_WALLET {mint[:6]} size={size:.4f}")

    if copy_signal:
        size *= 1.5
        engine.log(f"SIZE_COPY_SIGNAL {mint[:6]} size={size:.4f}")

    if w_cluster > 0.5:
        size *= 1.3
        engine.log(f"SIZE_CLUSTER_BOOST {mint[:6]} size={size:.4f}")

    if size <= 0:
        engine.log(f"SIZE_ZERO {mint[:6]}")
        return

    # 9. 風控
    if not allow(engine, score, size):
        engine.log(f"BLOCKED_ALLOW {mint[:6]}")
        return

    ok, reason = should_enter(
        token,
        {
            "momentum": 0.01,
            "smart_money": s,
        }
    )
    if not ok:
        engine.log(f"FILTERED {mint[:6]} {reason}")
        return

    # 10. 下單
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
            "insider": ins,
            "wallet": top_wallet,
            "top_wallet_count": top_wallet_count,
            "wallet_alpha_avg": w_avg,
            "wallet_alpha_best": w_best,
            "wallet_cluster": w_cluster,
            "wallet_copy_signal": copy_signal,
            "weights": weights,
        },
    )

    engine.log(f"WALLET_TRACK {top_wallet}")


async def main_loop():
    engine.log("🚀 ENGINE START")

    while engine.running:
        try:
            await manage_positions()

            tokens = await scan()
            engine.log(f"SCAN_COUNT {len(tokens)}")

            for t in tokens:
                recent_changes.append(float(t.get("change", 0)))

            engine.regime = detect_regime(recent_changes[-40:])
            engine.log(f"REGIME {engine.regime}")

            if engine.regime in ["flat", "trend_down"]:
                await asyncio.sleep(2)
                continue

            routes = router.build_routes(tokens)
            engine.log(f"ROUTE_COUNT {len(routes)}")

            for r in routes:
                await evaluate_route(r)

        except Exception as e:
            engine.log(f"LOOP ERR {e}")

        await asyncio.sleep(2)
