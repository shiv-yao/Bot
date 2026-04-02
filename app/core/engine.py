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
from app.alpha.combiner import combine_scores, get_dynamic_weights
from app.alpha.signal_router import router
from app.alpha.entry_filter import should_enter
from app.alpha.wallet_tracker import record_wallet_trade
from app.alpha.helius_wallet_tracker import update_token_wallets, token_wallets
from app.alpha.insider_engine import get_token_insider_score
from app.alpha.wallet_alpha import (
    record_wallet_result,
    get_best_wallet,
    get_top_wallets,
    get_token_wallet_alpha,
)

from app.portfolio.allocator import get_position_size
from app.portfolio.portfolio_manager import portfolio

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

    wallet_for_trade = meta.get("wallet")
    if wallet_for_trade is None:
        wallets = list(token_wallets.get(mint, set()))
        if wallets:
            wallet_for_trade = wallets[0]
        else:
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

    record_wallet_result(wallet, pnl)

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
    if wallet:
        record_wallet_result(wallet, pnl)

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

from app.alpha.wallet_alpha import get_token_wallet_alpha


from app.alpha.wallet_alpha_v7 import (
    get_wallet_alpha,
    record_token_wallets,
    record_wallet_trade,
)
from app.alpha.helius_smart_wallet import fetch_smart_wallets


async def evaluate_route(route):

    mint = route["mint"]
    token = route["token"]
    source = route.get("source", "unknown")

    # ===== 1️⃣ 抓鏈上 wallet =====
    wallets = await fetch_smart_wallets(mint)
    record_token_wallets(mint, wallets)

    if wallets:
        print(f"WALLET_OK {mint[:6]} {len(wallets)}")
    else:
        print(f"WALLET_EMPTY {mint[:6]}")

    # ===== 2️⃣ alpha =====
    b = breakout_score(token)
    l = liquidity_score(token)
    s = await smart_money_score(mint)
    ins = get_token_insider_score(mint)

    wa = get_wallet_alpha(mint)

    if not wa:
        print(f"REJECT_NO_WALLET {mint[:6]}")
        return

    w_avg = wa["avg"]
    w_best = wa["best"]
    w_cluster = wa["cluster"]
    copy = wa["copy_signal"]
    top_wallet = wa["top_wallet"]

    print(
        f"WALLET_ALPHA {mint[:6]} "
        f"avg={w_avg:.3f} "
        f"best={w_best:.3f} "
        f"cluster={w_cluster:.3f} "
        f"copy={copy}"
    )

    # ===== 3️⃣ v7 核心（wallet主導）=====
    score = (
        b * 0.25 +
        l * 0.10 +
        s * 0.20 +
        ins * 0.15 +
        w_avg * 0.30
    )

    print(
        f"ROUTE {mint[:6]} "
        f"score={score:.3f} "
        f"b={b:.3f} "
        f"s={s:.3f} "
        f"l={l:.3f} "
        f"ins={ins:.3f}"
    )

    # ===== 4️⃣ 強制過濾 =====
    if w_best < 0.2:
        print(f"REJECT_NO_SMART {mint[:6]}")
        return

    if w_cluster < 0.2:
        print(f"REJECT_NO_CLUSTER {mint[:6]}")
        return

    # ===== 5️⃣ size =====
    size = 0.01

    if w_best > 0.6:
        size *= 2

    if copy:
        size *= 1.5

    if w_cluster > 0.5:
        size *= 1.3

    print(
        f"BUY {mint[:6]} "
        f"size={size:.4f} "
        f"wallet={top_wallet}"
    )

    # ===== 6️⃣ 下單 =====
    price = await get_price(token)
    if not price:
        return

    buy(
        mint,
        price,
        score,
        size,
        {
            "source": source,
            "wallet": top_wallet,
            "wallet_alpha_avg": w_avg,
            "wallet_alpha_best": w_best,
            "wallet_cluster": w_cluster,
            "wallet_copy_signal": copy,
        },
    )



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
