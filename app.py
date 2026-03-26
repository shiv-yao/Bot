import asyncio

@app.on_event("startup")
async def start_bot():
    from bot import bot_loop

    print("🔥 STARTING BOT LOOP")
    asyncio.create_task(bot_loop())
