import asyncio
from state import engine

async def bot_loop():
    print("🚀 BOT LOOP STARTED")
    engine.logs.append("🚀 BOT LOOP STARTED")

    while True:
        print("⏱ RUNNING LOOP")
        engine.logs.append("⏱ RUNNING LOOP")

        engine.last_signal = "TEST"
        engine.last_trade = "TEST"

        await asyncio.sleep(5)
