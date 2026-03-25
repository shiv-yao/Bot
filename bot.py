import asyncio
import random

async def bot_loop():
    print("🚀 BOT STARTED")
    while True:
        score = random.random()
        if score > 0.8:
            print(f"📈 SIGNAL score={round(score,2)}")
        await asyncio.sleep(1)
