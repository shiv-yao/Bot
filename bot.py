import asyncio
from state import engine

async def bot_loop():
    engine.logs.append("🔥 BOT STARTED")

    while True:
        engine.logs.append("⏱ LOOP RUNNING")

        # 假裝交易（先確認有動）
        engine.last_signal = "TEST SIGNAL"
        engine.last_trade = "TEST TRADE"

        await asyncio.sleep(5)
