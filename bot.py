import asyncio

async def bot_loop():
    print("BOT LOOP STARTED")
    while True:
        print("BOT LOOP TICK")
        await asyncio.sleep(5)
