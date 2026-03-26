@app.on_event("startup")
async def start():
    try:
        from bot import bot_loop
        import asyncio
        asyncio.create_task(bot_loop())
    except Exception as e:
        print("BOT FAILED:", e)
