import asyncio
import time

from app.core.state import engine
from app.core.scanner import scan
from app.core.pricing import get_price
from app.core.risk import allow, kill_switch

from app.alpha.breakout import breakout_score
from app.alpha.smart_money import smart_money_score
from app.alpha.liquidity import liquidity_score
from app.alpha.regime import detect_regime
from app.alpha.combiner import combine_scores
from app.portfolio.allocator import get_position_size

TP = 0.045
SL = -0.008
TRAIL = 0.004

COOLDOWN = 30
MIN_MOMENTUM = 0.004
MIN_DIRECT_BUY = 0.70
MIN_CANDIDATE_BUY = 0.55

cooldown = {}
candidates = {}
recent_changes = []
last_trade_time = 0


def calc_drawdown():
    if engine.peak_capital <= 0:
        return 0.0
    return (engine.capital - engine.peak_capital) / engine.peak_capital


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
    })

    engine.stats["executed"] += 1
    last_trade_time = now

    engine.log(
        "BUY "
        f"{mint[:6]} "
        f"price={price:.4f} "
        f"size={size:.4f} "
        f"score={score:.4f} "
        f"regime={engine.regime} "
        f"b={meta['breakout']:.3f} "
        f"s={meta['smart_money']:.3f} "
        f"l={meta['liquidity']:.3f} "
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
        engine.stats["wins"] += 1
    else:
        engine.stats["losses"] += 1

    engine.log(
        f"SELL {pos['mint'][:6]} "
        f"{reason} pnl={pnl:.4f} "
        f"cap={engine.capital:.4f}"
    )


async def manage_positions():
    now = time.time()
    remaining = []

    for pos in engine.positions:
        try:
            price = await get_price(pos["mint"])

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

            # 只有浮盈到一定程度才啟動 trailing
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


async def evaluate_token(token: dict):
    mint = token["mint"]
    change = float(token.get("change", 0))

    engine.stats["signals"] += 1

    if mint in cooldown and time.time() - cooldown[mint] < COOLDOWN:
        engine.log(f"COOLDOWN {mint[:6]}")
        return

    if abs(change) < 2:
        engine.log(f"FLAT_SKIP {mint[:6]}")
        return

    if any(p["mint"] == mint for p in engine.positions):
        engine.log(f"ALREADY_HELD {mint[:6]}")
        return

    if time.time() - last_trade_time < 8:
        return

    # 三策略分數
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
        f"SCORE {mint[:6]} "
        f"final={score:.4f} "
        f"b={b:.3f} s={s:.3f} l={l:.3f} regime={engine.regime}"
    )

    if score < MIN_CANDIDATE_BUY:
        engine.stats["rejected"] += 1
        engine.log(f"REJECT {mint[:6]}")
        return

    if not allow(engine, score, 0.02):
        return

    price_now = await get_price(token)
    now = time.time()

    # 高分也要確認 momentum，不直接裸買
    if mint not in candidates:
        candidates[mint] = {
            "time": now,
            "price": price_now,
            "score": score,
            "breakout": b,
            "smart_money": s,
            "liquidity": l,
        }
        engine.log(f"CANDIDATE {mint[:6]}")
        return

    # 候選太久作廢
    if now - candidates[mint]["time"] > 10:
        del candidates[mint]
        engine.log(f"CANDIDATE_EXPIRE {mint[:6]}")
        return

    # 等待 2 秒再確認
    if now - candidates[mint]["time"] < 2:
        return

    old_price = candidates[mint]["price"]
    momentum = (price_now - old_price) / old_price

    if momentum < MIN_MOMENTUM:
        engine.log(f"STRONG_REJECT {mint[:6]}")
        del candidates[mint]
        return

    size = get_position_size(score, engine.capital, engine.regime)

    buy(
        mint=mint,
        price=price_now,
        score=score,
        size=size,
        meta={
            "breakout": b,
            "smart_money": s,
            "liquidity": l,
            "momentum": momentum,
        },
    )

    cooldown[mint] = now
    del candidates[mint]


async def main_loop():
    engine.log("ENGINE STARTED")

    while engine.running:
        try:
            if kill_switch(engine):
                break

            await manage_positions()

            if engine.capital > engine.peak_capital:
                engine.peak_capital = engine.capital

            drawdown = calc_drawdown()
            engine.log(f"DRAWDOWN {drawdown:.4f}")

            # 回撤保護
            if engine.capital < engine.peak_capital * 0.97:
                engine.log("LOSS_COOLDOWN")
                await asyncio.sleep(5)
                continue

            tokens = await scan()

            for t in tokens:
                recent_changes.append(float(t.get("change", 0)))

            if len(recent_changes) > 40:
                recent_changes = recent_changes[-40:]

            engine.regime = detect_regime(recent_changes)
            engine.log(f"REGIME {engine.regime}")

            # 平盤與空頭少打
            if engine.regime in {"flat", "trend_down"}:
                await asyncio.sleep(2)
                continue

            for token in tokens:
                await evaluate_token(token)

        except Exception as e:
            engine.stats["errors"] += 1
            engine.log(f"LOOP_ERR {e}")

        await asyncio.sleep(2)
