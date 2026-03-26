import os
import asyncio
from fastapi import FastAPI
from fastapi.responses import JSONResponse, HTMLResponse

app = FastAPI()

BOT_STATUS = {"ok": False, "error": ""}

@app.on_event("startup")
async def start_bot_safely():
    try:
        from bot import bot_loop
        asyncio.create_task(bot_loop())
        BOT_STATUS["ok"] = True
        BOT_STATUS["error"] = ""
    except Exception as e:
        BOT_STATUS["ok"] = False
        BOT_STATUS["error"] = str(e)
        print("BOT FAILED:", e)

@app.get("/health")
async def health():
    return {"ok": True, "bot_ok": BOT_STATUS["ok"], "bot_error": BOT_STATUS["error"]}

@app.get("/data")
async def data():
    return JSONResponse({
        "running": True,
        "mode": "PAPER",
        "sol_balance": 0.0,
        "capital": 0.0,
        "last_signal": "",
        "last_trade": "",
        "positions": [],
        "logs": [f"bot_ok={BOT_STATUS['ok']}", f"bot_error={BOT_STATUS['error']}"],
        "stats": {
            "signals": 0,
            "buys": 0,
            "sells": 0,
            "errors": 0
        },
        "trade_history": []
    })

@app.get("/", response_class=HTMLResponse)
async def home():
    return """
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Dashboard</title>
  <style>
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, sans-serif;
      background: #0b1020;
      color: #e5e7eb;
      padding: 20px;
    }
    .wrap { max-width: 900px; margin: 0 auto; }
    .card {
      background: #121a2f;
      border: 1px solid #1f2a44;
      border-radius: 14px;
      padding: 16px;
      margin-bottom: 14px;
    }
    .title { font-size: 28px; font-weight: 700; margin-bottom: 18px; }
    pre { white-space: pre-wrap; word-break: break-word; }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="title">⚔️ Dashboard</div>
    <div class="card"><pre id="out">loading...</pre></div>
  </div>
  <script>
    fetch('/data')
      .then(r => r.json())
      .then(d => {
        document.getElementById('out').textContent = JSON.stringify(d, null, 2);
      })
      .catch(e => {
        document.getElementById('out').textContent = 'fetch failed: ' + e;
      });
  </script>
</body>
</html>
"""

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
