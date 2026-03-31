# app/core/engine.py

import asyncio
import time
import random

from app.core import state
from app.core.scanner import fetch_tokens
from app.core.pricing import get_price
from app.core.execution import execute_buy, execute_sell
from app.core.risk import allow_trade

ENTRY_THRESHOLD = 0.02
TP = 0.04
SL = -0.025
TRAIL = 0.02
COOLDOWN = 60

def score(vol, change):
    return (change/100)*0.6 + min(vol/1e6,1)*0.3 + random.random()*0.003


def manage(prices):
    new_pos = []

    for p in state.positions:
        price = prices.get(p["mint"])
        if not price:
            new_pos.append(p)
            continue

        pnl = (price - p["entry"]) / p["entry"]

        if price > p["peak"]:
            p["peak"] = price

        dd = (price - p["peak"]) / p["peak"]

        state.logs.append(f"CHECK {p['mint'][:6]} pnl={pnl:.4f}")

        if pnl >= TP or pnl <= SL or dd <= -TRAIL:
            execute_sell(p, price)
            continue

        new_pos.append(p)

    state.positions = new_pos


async def main_loop():
    while True:
        try:
            tokens = await fetch_tokens()

            prices = {}
            for t in tokens:
                p = await get_price(t["mint"])
                if p:
                    prices[t["mint"]] = p

            manage(prices)

            for t in tokens:
                mint = t["mint"]

                state.stats["signals"] += 1

                if mint in state.cooldown and time.time() - state.cooldown[mint] < COOLDOWN:
                    continue

                p = prices.get(mint)
                if not p:
                    continue

                s = score(t["volume"], t["change"])

                if s < ENTRY_THRESHOLD:
                    state.stats["rejected"] += 1
                    continue

                ok, reason = allow_trade(state)
                if not ok:
                    state.logs.append(reason)
                    continue

                execute_buy(mint, p, 0.01)
                state.cooldown[mint] = time.time()

        except Exception as e:
            state.stats["errors"] += 1
            state.logs.append(f"ERR {e}")

        await asyncio.sleep(6)
