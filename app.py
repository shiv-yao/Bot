def start_bot_thread():
    def run():
        try:
            print("🔥 TRY IMPORT BOT")

            from bot import bot_loop

            print("🔥 IMPORT SUCCESS")

            asyncio.run(bot_loop())

        except Exception as e:
            import traceback
            print("💀 BOT ERROR:")
            traceback.print_exc()

            BOT_STATUS["ok"] = False
            BOT_STATUS["error"] = str(e)

    threading.Thread(target=run, daemon=True).start()
