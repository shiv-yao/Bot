import os
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from state import engine

BOT_TASK = None


# ================= INIT =================

def init_engine():
    engine.running = True
    engine.mode = getattr(engine, "mode", "PAPER")

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
            "errors": 0,
        }

    if not hasattr(engine, "last_trade"):
        engine.last_trade = ""

    if not hasattr(engine, "last_signal"):
        engine.last_signal = ""

    if not hasattr(engine, "capital"):
        engine.capital = 1.0

    if not hasattr(engine, "sol_balance"):
        engine.sol_balance = 1.0

    if not hasattr(engine, "bot_ok"):
        engine.bot_ok = True

    if not hasattr(engine, "bot_error"):
        engine.bot_error = ""

    if not hasattr(engine, "engine_stats"):
        engine.engine_stats = {
            "stable": {"pnl": 0.0, "trades": 0, "wins": 0},
            "degen": {"pnl": 0.0, "trades": 0, "wins": 0},
            "sniper": {"pnl": 0.0, "trades": 0, "wins": 0},
        }

    if not hasattr(engine, "engine_allocator"):
        engine.engine_allocator = {
            "stable": 0.4,
            "degen": 0.4,
            "sniper": 0.2,
        }

    if not hasattr(engine, "candidate_count"):
        engine.candidate_count = 0


# ================= BOT =================

async def start_bot():
    global BOT_TASK

    if BOT_TASK is not None and not BOT_TASK.done():
        return

    try:
        from bot import bot_loop
        BOT_TASK = asyncio.create_task(bot_loop())

        engine.bot_ok = True
        engine.bot_error = ""

        if not any("BOT_STARTED" in x for x in engine.logs[-5:]):
            engine.logs.append("BOT_STARTED")

    except Exception as e:
        engine.bot_ok = False
        engine.bot_error = str(e)
        engine.logs.append(f"BOT_ERROR {str(e)}")


# ================= LIFESPAN =================

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_engine()
    await start_bot()
    yield


app = FastAPI(lifespan=lifespan)


# ================= HELPERS =================

def normalize_position(p: dict):
    entry = p.get("entry_price", p.get("entry", 0))
    last = p.get("last_price", entry)
    peak = p.get("peak_price", p.get("peak", entry))

    pnl_pct = p.get("pnl_pct")
    if pnl_pct is None:
        pnl_pct = ((last - entry) / entry) if entry and entry > 0 else 0.0

    return {
        "token": p.get("token"),
        "amount": p.get("amount", 0),
        "entry_price": entry or 0,
        "last_price": last or 0,
        "peak_price": peak or 0,
        "pnl_pct": pnl_pct or 0,
        "engine": p.get("engine", ""),
        "alpha": p.get("alpha", 0),
    }


# ================= API =================

@app.get("/health")
def health():
    return {
        "ok": True,
        "bot_ok": engine.bot_ok,
        "bot_error": engine.bot_error,
    }


@app.get("/data")
def data():
    positions = [normalize_position(p) for p in engine.positions]

    return {
        "running": engine.running,
        "mode": engine.mode,
        "sol_balance": engine.sol_balance,
        "capital": engine.capital,
        "last_signal": engine.last_signal,
        "last_trade": engine.last_trade,
        "positions": positions,
        "logs": list(engine.logs)[-80:],
        "stats": dict(engine.stats),
        "trade_history": list(engine.trade_history)[-100:],
        "bot_ok": engine.bot_ok,
        "bot_error": engine.bot_error,
        "engine_stats": dict(getattr(engine, "engine_stats", {})),
        "engine_allocator": dict(getattr(engine, "engine_allocator", {})),
        "candidate_count": getattr(engine, "candidate_count", 0),
    }


# ================= UI =================

@app.get("/", response_class=HTMLResponse)
def home():
    return """
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Quant Dashboard</title>
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
.grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
}
.card {
  background: #121a2f;
  padding: 12px;
  border-radius: 10px;
  overflow: auto;
}
.full {
  grid-column: 1 / -1;
}
.two {
  grid-column: span 2;
}
h2 {
  margin-top: 0;
}
.small {
  font-size: 12px;
  color: #9fb0d1;
}
pre {
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
}
.row {
  margin-bottom: 8px;
}
@media (max-width: 900px) {
  .grid { grid-template-columns: 1fr 1fr; }
  .two { grid-column: span 2; }
}
@media (max-width: 520px) {
  .grid { grid-template-columns: 1fr; }
  .two, .full { grid-column: auto; }
}
</style>
</head>
<body>
<div class="wrap">
  <h2>Quant Dashboard v1300</h2>
  <div id="main"></div>
</div>

<script>
function renderEngineStats(es) {
  const keys = ["stable", "degen", "sniper"];
  return keys.map(k => {
    const v = es?.[k] || {};
    return `
      <div class="row">
        <b>${k}</b><br>
        pnl=${Number(v.pnl || 0).toFixed(6)} |
        trades=${v.trades ?? 0} |
        wins=${v.wins ?? 0}
      </div>
    `;
  }).join("");
}

function renderAllocator(a) {
  const keys = ["stable", "degen", "sniper"];
  return keys.map(k => {
    const v = a?.[k] ?? 0;
    return `<div class="row"><b>${k}</b>: ${Number(v).toFixed(3)}</div>`;
  }).join("");
}

async function load() {
  const d = await fetch('/data').then(r => r.json());

  const posHtml = (d.positions || []).length === 0
    ? 'No positions'
    : (d.positions || []).map(p => `
      <div style="margin-bottom:10px;">
        <b>${(p.token || '').slice(0, 12)}</b><br>
        amount=${Number(p.amount || 0).toFixed(4)}<br>
        entry=${Number(p.entry_price || 0).toExponential(4)}<br>
        last=${Number(p.last_price || 0).toExponential(4)}<br>
        peak=${Number(p.peak_price || 0).toExponential(4)}<br>
        pnl=${((p.pnl_pct || 0) * 100).toFixed(2)}%<br>
        engine=${p.engine || '-'} alpha=${Number(p.alpha || 0).toFixed(4)}
      </div>
    `).join('');

  const logsHtml = (d.logs || []).slice().reverse().map(x => `<div>${x}</div>`).join('');

  const histHtml = (d.trade_history || []).length === 0
    ? 'No trade history'
    : (d.trade_history || []).slice().reverse().map(x => `
      <div style="margin-bottom:8px;">
        <b>${x.side || 'TRADE'}</b> ${(x.mint || '').slice(0, 12)}
        <pre>${JSON.stringify(x.result || x, null, 2)}</pre>
      </div>
    `).join('');

  document.getElementById('main').innerHTML = `
    <div class="grid">
      <div class="card">
        <div class="small">Mode</div>
        <div>${d.mode || '-'}</div>
      </div>
      <div class="card">
        <div class="small">Capital</div>
        <div>${Number(d.capital || 0).toFixed(6)}</div>
      </div>
      <div class="card">
        <div class="small">SOL Balance</div>
        <div>${Number(d.sol_balance || 0).toFixed(6)}</div>
      </div>
      <div class="card">
        <div class="small">Bot Status</div>
        <div>${d.bot_ok ? 'OK' : 'ERROR'}</div>
      </div>

      <div class="card">
        <div class="small">Signals</div>
        <div>${d.stats?.signals ?? 0}</div>
      </div>
      <div class="card">
        <div class="small">Buys</div>
        <div>${d.stats?.buys ?? 0}</div>
      </div>
      <div class="card">
        <div class="small">Sells</div>
        <div>${d.stats?.sells ?? 0}</div>
      </div>
      <div class="card">
        <div class="small">Errors</div>
        <div>${d.stats?.errors ?? 0}</div>
      </div>

      <div class="card">
        <div class="small">Candidates</div>
        <div>${d.candidate_count ?? 0}</div>
      </div>
      <div class="card two">
        <div class="small">Last Signal</div>
        <div>${d.last_signal || '-'}</div>
      </div>
      <div class="card">
        <div class="small">Last Trade</div>
        <div>${d.last_trade || '-'}</div>
      </div>

      <div class="card two">
        <div class="small">Engine Allocator</div>
        <div>${renderAllocator(d.engine_allocator)}</div>
      </div>

      <div class="card two">
        <div class="small">Engine Stats</div>
        <div>${renderEngineStats(d.engine_stats)}</div>
      </div>

      <div class="card two">
        <div class="small">Positions</div>
        <div>${posHtml}</div>
      </div>

      <div class="card two">
        <div class="small">Logs</div>
        <div>${logsHtml || 'No logs'}</div>
      </div>

      <div class="card full">
        <div class="small">Trade History</div>
        <div>${histHtml}</div>
      </div>

      <div class="card full">
        <div class="small">Bot Error</div>
        <div>${d.bot_error || '-'}</div>
      </div>
    </div>
  `;
}

load();
setInterval(load, 2000);
</script>
</body>
</html>
    """


# ================= RUN =================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
