import os
import asyncio
import traceback
from contextlib import asynccontextmanager
from collections import deque

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from state import engine

BOT_TASK = None


def to_jsonable(obj):
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    if isinstance(obj, deque):
        return [to_jsonable(x) for x in list(obj)]
    if isinstance(obj, list):
        return [to_jsonable(x) for x in obj]
    if isinstance(obj, tuple):
        return [to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    return str(obj)


def safe_snapshot():
    try:
        snap = engine.snapshot()
    except Exception as e:
        return {
            "running": getattr(engine, "running", True),
            "mode": getattr(engine, "mode", "PAPER"),
            "sol_balance": getattr(engine, "sol_balance", 0.0),
            "capital": getattr(engine, "capital", 0.0),
            "last_signal": getattr(engine, "last_signal", ""),
            "last_trade": getattr(engine, "last_trade", ""),
            "positions": to_jsonable(getattr(engine, "positions", [])),
            "logs": to_jsonable(list(getattr(engine, "logs", []))),
            "stats": to_jsonable(getattr(engine, "stats", {})),
            "trade_history": to_jsonable(getattr(engine, "trade_history", [])),
            "bot_ok": False,
            "bot_error": f"snapshot failed: {e}",
            "engine_stats": to_jsonable(getattr(engine, "engine_stats", {})),
            "engine_allocator": to_jsonable(getattr(engine, "engine_allocator", {})),
            "candidate_count": getattr(engine, "candidate_count", 0),
        }
    return to_jsonable(snap)


async def start_bot():
    global BOT_TASK

    if BOT_TASK is not None and not BOT_TASK.done():
        return

    try:
        from bot import bot_loop
        BOT_TASK = asyncio.create_task(bot_loop(), name="bot_loop_task")
        try:
            engine.bot_ok = True
            engine.bot_error = ""
        except Exception:
            pass
    except Exception as e:
        try:
            engine.bot_ok = False
            engine.bot_error = str(e)
            if hasattr(engine, "logs"):
                engine.logs.append(f"BOT_START_ERROR {e}")
        except Exception:
            pass


async def stop_bot():
    global BOT_TASK

    if BOT_TASK is None:
        return

    if not BOT_TASK.done():
        BOT_TASK.cancel()
        try:
            await BOT_TASK
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    BOT_TASK = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    await start_bot()
    try:
        yield
    finally:
        await stop_bot()


app = FastAPI(lifespan=lifespan)


@app.get("/health")
def health():
    return {
        "ok": True,
        "task_running": BOT_TASK is not None and not BOT_TASK.done(),
        "bot_ok": getattr(engine, "bot_ok", True),
        "bot_error": getattr(engine, "bot_error", ""),
    }


@app.get("/data")
def data():
    try:
        return JSONResponse(content=safe_snapshot())
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": str(e),
                "traceback": traceback.format_exc()[-4000:],
            },
        )


@app.post("/restart")
async def restart():
    await stop_bot()
    await start_bot()
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def home():
    return """
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>v1307 Dashboard</title>
<style>
body {
  background: #0b1020;
  color: #fff;
  font-family: sans-serif;
  padding: 16px;
  margin: 0;
}
.wrap {
  max-width: 1200px;
  margin: 0 auto;
}
button {
  background: #1f6feb;
  color: white;
  border: 0;
  border-radius: 8px;
  padding: 10px 14px;
  cursor: pointer;
  margin-right: 8px;
}
pre {
  white-space: pre-wrap;
  word-break: break-word;
  background: #121a2f;
  padding: 12px;
  border-radius: 10px;
}
.err {
  color: #ff8080;
}
</style>
</head>
<body>
<div class="wrap">
  <h2>v1307 Dashboard</h2>
  <div style="margin-bottom:12px;">
    <button onclick="restartBot()">Restart Bot</button>
    <button onclick="load()">Refresh</button>
  </div>
  <div id="status"></div>
  <pre id="data">loading...</pre>
</div>

<script>
async function restartBot() {
  try {
    await fetch('/restart', { method: 'POST' });
    setTimeout(load, 1000);
  } catch (e) {
    document.getElementById('status').innerHTML = '<div class="err">restart failed: ' + e + '</div>';
  }
}

async function load() {
  const statusEl = document.getElementById('status');
  const dataEl = document.getElementById('data');

  try {
    const r = await fetch('/data');
    const d = await r.json();

    if (!r.ok) {
      statusEl.innerHTML = '<div class="err">/data failed</div>';
      dataEl.textContent = JSON.stringify(d, null, 2);
      return;
    }

    statusEl.innerHTML = '';
    dataEl.textContent = JSON.stringify(d, null, 2);
  } catch (e) {
    statusEl.innerHTML = '<div class="err">fetch failed: ' + e + '</div>';
    dataEl.textContent = 'fetch failed';
  }
}

load();
setInterval(load, 2000);
</script>
</body>
</html>
    """


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
