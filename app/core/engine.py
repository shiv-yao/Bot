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
from app.alpha.combiner import combine_scores
from app.alpha.signal_router import router

from app.portfolio.allocator import get_position_size
from app.portfolio.portfolio_manager import portfolio

TP = 0.045
SL = -0.008
TRAIL = 0.004

COOLDOWN = 30
MIN_MOMENTUM = 0.004
MIN_CANDIDATE_BUY = 0.55
TRADE_INTERVAL = 12

cooldown = {}
candidates = {}
recent_changes = []
last_trade_time = 0


def calc_drawdown() -> float:
    if engine.peak_capital <= 0:
        return 0.0
    return (engine.capital - engine.peak_capital) / engine.peak_capital


def portfolio_can_add_more() -> bool:
    return portfolio.can_add_more(engine, max_exposure=0.75)


def buy(mint: str, price: float, score: float, size: float, meta: dict):
    global last_trade_time

    risk_adj = 1.0
    total = engine.stats.get("wins", 0) + engine.stats.get("losses", 0)
    if total >= 5:
        winrate = engine.stats.get("wins", 0) / max(total, 1)
        if winrate > 0.65:
            risk_adj = 1.2
        elif winrate < 0.45:
            risk_adj = 0.7

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
    })

    engine.stats["executed"] += 1
    last_trade_time = now
    risk_engine.record_trade()

    engine.log(
        "BUY "
        f"{mint[:6]} "
        f"price={price:.4f} "
        f"size={size:.4f} "
        f"score={score:.4f} "
        f"risk_adj={risk_adj:.2f} "
        f"regime={engine.regime} "
        f"src={meta['source']} "
        f"b={meta['breakout']:.3f} "
        f"s={meta['smart_money']:.3f} "
        f"l={meta['liquidity']:.3f} "
        f"mom={meta['momentum']:.4f} "
        f"cap={engine.capital:.4f}"
    )


def sell(pos: dict, price: float, reason: str):
    pnl = (price - pos["entry"]) / pos["entry"]
    engine.capital += pos["size"] * (1 + pnl)

    trade = {
        "mint": pos["mint"],
        "entry": pos["entry"],
        "exit": price,
        "pnl": pnl,
        "size": pos["size"],
        "reason": reason,
        "score": pos.get("score"),
        "meta": pos.get("meta", {}),
    }
    engine.trade_history.append(trade)

    if pnl >= 0:
        engine.stats["wins"] = engine.stats.get("wins", 0) + 1
    else:
        engine.stats["losses"] = engine.stats.get("losses", 0) + 1

    risk_engine.record_realized(pnl)

    if pnl < 0 and risk_engine.drawdown(engine.capital) > 0.10:
        risk_engine.trigger_cooldown(90)

    engine.log(
        f"SELL {pos['mint'][:6]} "
        f"{reason} pnl={pnl:.4f} cap={engine.capital:.4f}"
    )


async def manage_positions():
    now = time.time()
    remaining = []

    for pos in engine.positions:
        try:
            price = await get_price(pos["mint"])
            if price is None:
                remaining.append(pos)
                continue

            if price > pos["peak"]:
                pos["peak"] = price

            pnl = (price - pos["entry"]) / pos["entry"]
            dd = (price - pos["peak"]) / pos["peak"]

            engine.log(f"CHECK {pos['mint'][:6]} pnl={pnl:.4f} dd={dd:.4f}")

            if pnl >= TP:
                sell(pos, price, "TP")
                continue

            if pnl <= SL:
                sell(pos, price, "SL")
                continue

            if pnl >= 0.015 and dd <= -TRAIL:
                sell(pos, price, "TRAIL")
                continue

            if now - pos["time"] > 50:
                sell(pos, price, "TIME")
                continue

            remaining.append(pos)

        except Exception as e:
            engine.stats["errors"] += 1
            engine.log(f"MANAGE_ERR {e}")
            remaining.append(pos)

    engine.positions = remaining


async def evaluate_route(route: dict):
    global last_trade_time

    token = route["token"]
    mint = route["mint"]
    source = route["source"]
    route_score = float(route.get("score", 0.0) or 0.0)

    change = float(token.get("change", 0))
    now = time.time()

    engine.stats["signals"] += 1

    if mint in cooldown and now - cooldown[mint] < COOLDOWN:
        engine.log(f"COOLDOWN {mint[:6]}")
        return

    if abs(change) < 2:
        engine.log(f"FLAT_SKIP {mint[:6]}")
        return

    if any(p["mint"] == mint for p in engine.positions):
        engine.log(f"ALREADY_HELD {mint[:6]}")
        return

    if now - last_trade_time < TRADE_INTERVAL:
        return

    # 三策略重新計分，保留完整 meta
    b = breakout_score(token)
    s = smart_money_score(token)
    l = liquidity_score(token)

    score = combine_scores(
        breakout=b,
        smart_money=s,
        liquidity=l,
        regime=engine.regime,
    )

    engine.log(
        f"ROUTE {mint[:6]} "
        f"src={source} "
        f"route={route_score:.3f} "
        f"final={score:.3f} "
        f"b={b:.3f} s={s:.3f} l={l:.3f} "
        f"regime={engine.regime}"
    )

    if score < MIN_CANDIDATE_BUY:
        engine.stats["rejected"] += 1
        engine.log(f"REJECT {mint[:6]}")
        return

    price_now = await get_price(token)
    if price_now is None:
        engine.log(f"NO_PRICE {mint[:6]}")
        return

    if mint not in candidates:
        candidates[mint] = {
            "time": now,
            "price": price_now,
            "score": score,
            "source": source,
            "breakout": b,
            "smart_money": s,
            "liquidity": l,
        }
        engine.log(f"CANDIDATE {mint[:6]}")
        return

    if now - candidates[mint]["time"] > 10:
        del candidates[mint]
        engine.log(f"CANDIDATE_EXPIRE {mint[:6]}")
        return

    if now - candidates[mint]["time"] < 2:
        return

    old_price = candidates[mint]["price"]
    momentum = (price_now - old_price) / old_price

    if momentum < MIN_MOMENTUM:
        engine.log(f"STRONG_REJECT {mint[:6]}")
        del candidates[mint]
        return

    # 雙層 sizing：score allocator + portfolio manager
    base_size = get_position_size(score, engine.capital, engine)
    portfolio_size = portfolio.weighted_position_size(
        engine=engine,
        source=source,
        base_risk_pct=0.08,
        max_position_size=0.12,
        min_position_size=0.02,
    )
    size = min(base_size, portfolio_size)

    if not allow(engine, score, size):
        del candidates[mint]
        return

    allow_trade, reason = risk_engine.allow_trade(
        equity=engine.capital,
        loss_streak=engine.stats.get("losses", 0),
        portfolio_can_add_more=portfolio_can_add_more(),
    )
    if not allow_trade:
        engine.log(f"BLOCKED {reason}")
        del candidates[mint]
        return

    buy(
        mint=mint,
        price=price_now,
        score=score,
        size=size,
        meta={
            "source": source,
            "route_score": route_score,
            "breakout": b,
            "smart_money": s,
            "liquidity": l,
            "momentum": momentum,
        },
    )

    cooldown[mint] = now
    del candidates[mint]


async def main_loop():
    global recent_changes

    engine.log("ENGINE STARTED")

    while engine.running:
        try:
            risk_engine.update(engine.capital)

            await manage_positions()

            if engine.capital > engine.peak_capital:
                engine.peak_capital = engine.capital

            drawdown = calc_drawdown()
            engine.log(f"DRAWDOWN {drawdown:.4f}")

            if engine.capital < engine.peak_capital * 0.95:
                engine.log("HARD_COOLDOWN")
                await asyncio.sleep(10)
                continue

            allow_trade, reason = risk_engine.allow_trade(
                equity=engine.capital,
                loss_streak=engine.stats.get("losses", 0),
                portfolio_can_add_more=portfolio_can_add_more(),
            )
            if not allow_trade:
                engine.log(f"BLOCKED {reason}")
                await asyncio.sleep(2)
                continue

            tokens = await scan()

            for t in tokens:
                recent_changes.append(float(t.get("change", 0)))

            if len(recent_changes) > 40:
                recent_changes = recent_changes[-40:]

            engine.regime = detect_regime(recent_changes)
            engine.log(f"REGIME {engine.regime}")

            if engine.regime in {"flat", "trend_down"}:
                await asyncio.sleep(2)
                continue

            routes = router.build_routes(tokens)

            for route in routes:
                await evaluate_route(route)

        except Exception as e:
            engine.stats["errors"] += 1
            engine.log(f"LOOP_ERR {e}")

        await asyncio.sleep(2)
