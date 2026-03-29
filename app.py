import os
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from state import engine

BOT_STATUS = {"ok": False, "error": ""}

# ================= INIT FIX =================

def init_engine():
    engine.running = True
    engine.mode = getattr(engine, "mode", "PAPER")
    engine.bot_ok = True
    engine.bot_error = ""

    if not hasattr(engine, "positions"):
        engine.positions = []

    if not hasattr(engine, "logs"):
        engine.logs = []

    if not hasattr(engine, "trade_history"):
        engine.trade_history = []

    if not hasattr(engine, "stats"):
        engine.stats = {
            "signals": 0,
            "buys": 0,
            "sells": 0,
            "errors": 0
        }

    if not hasattr(engine, "last_trade"):
        engine.last_trade = ""

    if not hasattr(engine, "last_signal"):
        engine.last_signal = ""

    if not hasattr(engine, "capital"):
        engine.capital = 1.0

    if not hasattr(engine, "sol_balance"):
        engine.sol_balance = 1.0


# ================= BOT START =================

BOT_TASK = None

async def start_bot():
    global BOT_TASK

    try:
        from bot import bot_loop

        if BOT_TASK is None:
            BOT_TASK = asyncio.create_task(bot_loop())

        BOT_STATUS["ok"] = True
        BOT_STATUS["error"] = ""

        engine.bot_ok = True
        engine.bot_error = ""

    except Exception as e:
        BOT_STATUS["ok"] = False
        BOT_STATUS["error"] = str(e)

        engine.bot_ok = False
        engine.bot_error = str(e)

        engine.logs.append(f"BOT_START_ERROR {str(e)}")


# ================= LIFESPAN =================

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_engine()
    await start_bot()
    yield


app = FastAPI(lifespan=lifespan)

# ================= API =================

@app.get("/health")
def health():
    return {
        "ok": True,
        "bot_ok": BOT_STATUS["ok"],
        "bot_error": BOT_STATUS["error"],
    }


@app.get("/data")
def data():

    # 👉 UI 對齊修正
    positions = []
    for p in engine.positions:
        entry = p.get("entry", 0)
        peak = p.get("peak", entry)

        last = p.get("last_price", peak)

        pnl_pct = 0
        if entry > 0:
            pnl_pct = (last - entry) / entry

        positions.append({
            "token": p.get("token"),
            "amount": p.get("amount"),
            "entry_price": entry,
            "last_price": last,
            "peak_price": peak,
            "pnl_pct": pnl_pct
        })

    return {
        "running": engine.running,
        "mode": engine.mode,
        "sol_balance": engine.sol_balance,
        "capital": engine.capital,
        "last_signal": engine.last_signal,
        "last_trade": engine.last_trade,
        "positions": positions,
        "logs": list(engine.logs)[-50:],
        "stats": dict(engine.stats),
        "trade_history": list(engine.trade_history)[-100:],
        "bot_ok": engine.bot_ok,
        "bot_error": engine.bot_error,
    }


# ================= UI =================

@app.get("/", response_class=HTMLResponse)
def home():
    return """<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>Quant Dashboard</title>
<style>
body { background:#0b1020;color:#fff;font-family:sans-serif;padding:20px;}
.card {background:#121a2f;padding:12px;margin:6px;border-radius:10px;}
</style>
</head>
<body>

<h2>🚀 Quant Dashboard</h2>

<div id="main"></div>

<script>
async function load(){
  const d = await fetch('/data').then(r=>r.json());

  document.getElementById('main').innerHTML = `
  <div class="card">Capital: ${d.capital}</div>
  <div class="card">Last Trade: ${d.last_trade}</div>
  <div class="card">Signals: ${d.stats.signals}</div>
  <div class="card">Buys: ${d.stats.buys}</div>
  <div class="card">Sells: ${d.stats.sells}</div>

  <div class="card">
    Positions:<br>
    ${d.positions.map(p=>`
      ${p.token?.slice(0,6)} pnl=${(p.pnl_pct*100).toFixed(2)}%
    `).join("<br>")}
  </div>

  <div class="card">
    Logs:<br>
    ${(d.logs||[]).slice(-10).join("<br>")}
  </div>
  `;
}

setInterval(load,2000)
load()
</script>

</body>
</html>"""
