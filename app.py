import os
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

BOT_STATUS = {"ok": False, "error": ""}

async def start_bot():
    try:
        print("🔥 IMPORT BOT")
        from bot import bot_loop
        print("🔥 BOT START")
        asyncio.create_task(bot_loop())   # ✅ 正確做法
        BOT_STATUS["ok"] = True
    except Exception as e:
        import traceback
        print("💀 BOT ERROR")
        traceback.print_exc()
        BOT_STATUS["ok"] = False
        BOT_STATUS["error"] = str(e)

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 APP START")
    await start_bot()
    yield
    print("🛑 APP STOP")

app = FastAPI(lifespan=lifespan)

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
    <h1>🚀 Dashboard</h1>
    <pre id="out"></pre>
    <script>
    async function load(){
        const r = await fetch('/data');
        const d = await r.json();
        document.getElementById('out').textContent =
            JSON.stringify(d, null, 2);
    }
    load(); setInterval(load, 2000);
    </script>
    """

@app.get("/health")
def health():
    return {"ok": True, "bot_ok": BOT_STATUS["ok"], "bot_error": BOT_STATUS["error"]}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
