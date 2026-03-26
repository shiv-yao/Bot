import os
import threading
import asyncio
from fastapi import FastAPI
from fastapi.responses import JSONResponse, HTMLResponse

app = FastAPI()

BOT_STATUS = {"ok": False, "error": ""}

def start_bot_thread():
    def run():
        try:
            from bot import bot_loop
            asyncio.run(bot_loop())
        except Exception as e:
            BOT_STATUS["ok"] = False
            BOT_STATUS["error"] = str(e)
            print("BOT CRASH:", e)

    t = threading.Thread(target=run, daemon=True)
    t.start()

@app.on_event("startup")
def startup():
    BOT_STATUS["ok"] = True
    start_bot_thread()

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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
