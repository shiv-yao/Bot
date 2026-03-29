import os
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from state import engine

BOT_TASK = None

async def start_bot():
    global BOT_TASK
    if BOT_TASK and not BOT_TASK.done():
        return

    from bot import bot_loop
    BOT_TASK = asyncio.create_task(bot_loop())

@asynccontextmanager
async def lifespan(app: FastAPI):
    await start_bot()
    yield

app = FastAPI(lifespan=lifespan)

@app.get("/data")
def data():
    return engine.snapshot()

@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <html>
    <body style="background:black;color:white">
    <h2>v1307 Dashboard</h2>
    <pre id="d"></pre>
    <script>
    async function load(){
      let d=await fetch('/data').then(r=>r.json())
      document.getElementById('d').innerText=JSON.stringify(d,null,2)
    }
    setInterval(load,2000)
    </script>
    </body>
    </html>
    """

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
