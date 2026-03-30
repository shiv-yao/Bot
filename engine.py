import asyncio, time
from app.storage import *
from app.risk import *
from app.alpha import *

positions = load_positions()
state = load_state()

async def engine_loop():

    while True:
        if not risk_guard(state["equity"]):
            print("KILL SWITCH")
            await asyncio.sleep(5)
            continue

        # 這裡接你原本 strategy_engine
        # score = ...

        # 模擬
        score = 0.05

        if score > 0.03:
            positions.append({
                "entry": 1.0,
                "size": 1
            })
            save_positions(positions)

        # monitor
        for p in positions[:]:
            pnl = 0.01  # 模擬
            state["equity"] += pnl

            update_loss(pnl)

            append_trade({
                "pnl": pnl,
                "time": time.time()
            })

            positions.remove(p)

        save_positions(positions)
        save_state(state)

        await asyncio.sleep(2)
