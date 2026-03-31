import asyncio
import time

from app.core.state import engine
from app.core.scanner import scan
from app.core.pricing import get_price
from app.core.risk import allow, kill_switch

TP = 0.02
SL = -0.01
TRAIL = 0.004   # 🔥 收緊，避免賺了又吐回去
COOLDOWN = 20
BASE_SIZE = 0.1

cooldown = {}


def score_token(token: dict) -> float:
    volume = float(token.get("volume", 0))
    change = float(token.get("change", 0))

    vol_score = min(volume / 100000.0, 1.0) * 0.4
    change_score = min(change / 10.0, 1.0) * 0.6
    return vol_score + change_score


def buy(mint: str, price: float):
    engine.capital -= BASE_SIZE
    engine.positions.append({
        "mint": mint,
        "entry": price,
        "peak": price,
        "size": BASE_SIZE,
        "time": time.time(),
    })
    engine.stats["executed"] += 1
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

            # 最長持有 30 秒，避免卡倉
            if now - pos["time"] > 30:
                sell(pos, price, "TIME")
                continue

            remaining.append(pos)

        except Exception as e:
            engine.stats["errors"] += 1
            engine.log(f"MANAGE_ERR {e}")
            remaining.append(pos)

    engine.positions = remaining


async def main_loop():
    engine.log("ENGINE STARTED")

    while engine.running:
        try:
            # 🔥 保命總開關
            if kill_switch(engine):
                break

            await manage_positions()

            # 🔥 記錄 peak capital + drawdown
            if engine.capital > engine.peak_capital:
                engine.peak_capital = engine.capital

            drawdown = (
                (engine.capital - engine.peak_capital) / engine.peak_capital
                if engine.peak_capital > 0
                else 0.0
            )
            engine.log(f"DRAWDOWN {drawdown:.4f}")

            tokens = await scan()

            for token in tokens:
                mint = token["mint"]
                change = float(token.get("change", 0))

                engine.stats["signals"] += 1

                if mint in cooldown and time.time() - cooldown[mint] < COOLDOWN:
                    engine.log(f"COOLDOWN {mint[:6]}")
                    continue

                # 🔥 震盪盤直接跳過
                if abs(change) < 2:
                    engine.log(f"FLAT_SKIP {mint[:6]}")
                    continue

                s = score_token(token)
                engine.log(f"SCORE {mint[:6]} {s:.4f}")

                # 🔥 門檻提高，減少亂買
                if s < 0.45:
                    engine.stats["rejected"] += 1
                    engine.log(f"REJECT {mint[:6]}")
                    continue

                if any(p["mint"] == mint for p in engine.positions):
                    engine.log(f"ALREADY_HELD {mint[:6]}")
                    continue

                if not allow(engine):
                    continue

                price = await get_price(token)
                buy(mint, price)
                cooldown[mint] = time.time()

        except Exception as e:
            engine.stats["errors"] += 1
            engine.log(f"LOOP_ERR {e}")

        await asyncio.sleep(3)
