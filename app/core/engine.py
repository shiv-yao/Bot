import asyncio
import time

from app.core.state import engine
from app.core.scanner import fetch_tokens
from app.core.pricing import get_price
from app.core.execution import buy, sell
from app.core.risk import allow

from app.alpha.alpha_engine import compute_alpha
from app.alpha.smart_wallet import flow_score, update_flow
from app.portfolio.allocator import dynamic_size

TP = 0.04
SL = -0.02
TRAIL = 0.02
COOLDOWN = 60

cooldown = {}

def manage(prices):
    new = []

    for p in engine.positions:
        price = prices.get(p["mint"])
        if not price:
            new.append(p)
            continue

        pnl = (price - p["entry"]) / p["entry"]

        if price > p["peak"]:
            p["peak"] = price

        dd = (price - p["peak"]) / p["peak"]

        engine.log(f"CHECK {p['mint'][:6]} pnl={pnl:.4f}")

        if pnl >= TP or pnl <= SL or dd <= -TRAIL:
            sell(p, price)
            continue

        new.append(p)

    engine.positions = new


async def main_loop():
    while True:
        try:
            update_flow()

            tokens = await fetch_tokens()

            prices = {}
            for t in tokens:
                p = await get_price(t["mint"])
                if p:
                    prices[t["mint"]] = p

            manage(prices)

            for t in tokens:
                engine.stats["signals"] += 1

                mint = t["mint"]

                if mint in cooldown and time.time() - cooldown[mint] < COOLDOWN:
                    continue

                price = prices.get(mint)
                if not price:
                    continue

                flow = flow_score()

                s = compute_alpha(
                    t["volume"],
                    t["change"],
                    flow
                )

                if s < 0.02:
                    engine.stats["rejected"] += 1
                    continue

                if not allow(engine):
                    continue

                size = dynamic_size(s, engine.capital)

                buy(mint, price, size)

                cooldown[mint] = time.time()

        except Exception as e:
            engine.stats["errors"] += 1
            engine.log(f"ERR {e}")

        await asyncio.sleep(6)
