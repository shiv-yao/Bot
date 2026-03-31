import asyncio
import time

from app.core.state import engine
from app.core.scanner import scan
from app.core.pricing import get_price
from app.core.risk import allow, kill_switch

TP = 0.035
SL = -0.008
TRAIL = 0.005

COOLDOWN = 25
BASE_SIZE = 0.1

cooldown = {}
candidates = {}

recent_changes = []
last_trade_time = 0


def score_token(token: dict) -> float:
    volume = float(token.get("volume", 0))
    change = float(token.get("change", 0))

    vol_score = min(volume / 100000.0, 1.0) * 0.4
    change_score = min(change / 10.0, 1.0) * 0.6

    return vol_score + change_score


def buy(mint: str, price: float):
    global last_trade_time

    engine.capital -= BASE_SIZE

    engine.positions.append({
        "mint": mint,
        "entry": price,
        "peak": price,
        "size": BASE_SIZE,
        "time": time.time(),
    })

    engine.stats["executed"] += 1
    last_trade_time = time.time()

    engine.log(f"BUY {mint[:6]} price={price:.4f} cap={engine.capital:.4f}")


def sell(pos: dict, price: float, reason: str):
    pnl = (price - pos["entry"]) / pos["entry"]
    engine.capital += pos["size"] * (1 + pnl)

    engine.log(
        f"SELL {pos['mint'][:6]} {reason} pnl={pnl:.4f} cap={engine.capital:.4f}"
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

            if pos["peak"] > pos["entry"] and dd <= -TRAIL:
                sell(pos, price, "TRAIL")
                continue

            if now - pos["time"] > 40:
                sell(pos, price, "TIME")
                continue

            remaining.append(pos)

        except Exception as e:
            engine.stats["errors"] += 1
            engine.log(f"MANAGE_ERR {e}")
            remaining.append(pos)

    engine.positions = remaining


async def main_loop():
    global recent_changes

    engine.log("ENGINE STARTED")

    while engine.running:
        try:
            if kill_switch(engine):
                break

            await manage_positions()

            # =========================
            # 🔥 Drawdown
            # =========================
            if engine.capital > engine.peak_capital:
                engine.peak_capital = engine.capital

            dd = (
                (engine.capital - engine.peak_capital)
                / engine.peak_capital
            )
            engine.log(f"DRAWDOWN {dd:.4f}")

            tokens = await scan()

            # =========================
            # 🔥 市場狀態判斷
            # =========================
            for t in tokens:
                recent_changes.append(float(t.get("change", 0)))

            if len(recent_changes) > 30:
                recent_changes = recent_changes[-30:]

            if len(recent_changes) > 5:
                market_vol = sum(abs(x) for x in recent_changes) / len(recent_changes)

                if market_vol < 1.5:
                    engine.log("MARKET_FLAT")
                    await asyncio.sleep(2)
                    continue

            # =========================

            for token in tokens:
                mint = token["mint"]
                change = float(token.get("change", 0))

                engine.stats["signals"] += 1

                # cooldown
                if mint in cooldown and time.time() - cooldown[mint] < COOLDOWN:
                    engine.log(f"COOLDOWN {mint[:6]}")
                    continue

                # 過濾震盪 token
                if abs(change) < 2:
                    engine.log(f"FLAT_SKIP {mint[:6]}")
                    continue

                score = score_token(token)
                engine.log(f"SCORE {mint[:6]} {score:.4f}")

                if score < 0.5:
                    engine.log(f"REJECT {mint[:6]}")
                    engine.stats["rejected"] += 1
                    continue

                if any(p["mint"] == mint for p in engine.positions):
                    continue

                if not allow(engine):
                    continue

                # =========================
                # 🔥 控制交易頻率
                # =========================
                if time.time() - last_trade_time < 5:
                    continue

                # =========================
                # 🔥 延遲進場（V10）
                # =========================
                now = time.time()

                if mint not in candidates:
                    candidates[mint] = {
                        "time": now,
                        "price": await get_price(token),
                    }
                    engine.log(f"CANDIDATE {mint[:6]}")
                    continue

                if now - candidates[mint]["time"] < 3:
                    continue

                price_now = await get_price(token)
                price_old = candidates[mint]["price"]

                # 🔥 強 momentum
                momentum = (price_now - price_old) / price_old

                if momentum < 0.003:
                    engine.log(f"WEAK_MOMENTUM {mint[:6]}")
                    del candidates[mint]
                    continue

                buy(mint, price_now)
                cooldown[mint] = now

                del candidates[mint]

        except Exception as e:
            engine.stats["errors"] += 1
            engine.log(f"LOOP_ERR {e}")

        await asyncio.sleep(2)
