import os
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from state import engine

BOT_STATUS = {"ok": False, "error": ""}

async def start_bot():
    try:
        from bot import bot_loop
        asyncio.create_task(bot_loop())
        BOT_STATUS["ok"] = True
    except Exception as e:
        BOT_STATUS["ok"] = False
        BOT_STATUS["error"] = str(e)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await start_bot()
    yield

app = FastAPI(lifespan=lifespan)

@app.get("/health")
def health():
    return {
        "ok": True,
        "bot_ok": BOT_STATUS["ok"],
        "bot_error": BOT_STATUS["error"],
    }

@app.get("/data")
def data():
    return {
        "running": engine.running,
        "mode": engine.mode,
        "sol_balance": engine.sol_balance,
        "capital": engine.capital,
        "last_signal": engine.last_signal,
        "last_trade": engine.last_trade,
        "positions": engine.positions,
        "logs": engine.logs[-20:],
        "stats": engine.stats,
        "trade_history": engine.trade_history[-20:],
        "bot_ok": BOT_STATUS["ok"],
        "bot_error": BOT_STATUS["error"],
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
        document.getElementById('out').textContent = JSON.stringify(d, null, 2);
    }
    load();
    setInterval(load, 2000);
    </script>
    """

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
