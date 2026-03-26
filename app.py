import os
import threading
import asyncio
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI()

BOT_STATUS = {"ok": False, "error": ""}

def run_bot():
    try:
        print("🔥 IMPORT BOT")
        from bot import bot_loop

        print("🔥 START BOT LOOP")
        asyncio.run(bot_loop())

    except Exception as e:
        import traceback
        print("💀 BOT CRASH:")
        traceback.print_exc()
        BOT_STATUS["ok"] = False
        BOT_STATUS["error"] = str(e)

@app.on_event("startup")
def startup():
    print("🚀 APP START")

    t = threading.Thread(target=run_bot, daemon=True)
    t.start()

    BOT_STATUS["ok"] = True


@app.get("/data")
def data():
    return {
        "running": True,
        "mode": "PAPER",
        "logs": [
            f"bot_ok={BOT_STATUS['ok']}",
            f"bot_error={BOT_STATUS['error']}"
        ]
    }


@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <h1>Dashboard</h1>
    <pre id="out"></pre>
    <script>
    async function load(){
        const r = await fetch('/data');
        const d = await r.json();
        document.getElementById('out').textContent =
            JSON.stringify(d, null, 2);
    }
    load(); setInterval(load,2000);
    </script>
    """
