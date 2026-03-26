import asyncio

async def bot_loop():
    print("🚀 BOT STARTED")

    while True:
        print("⏱ LOOP RUNNING")
        await asyncio.sleep(5)
