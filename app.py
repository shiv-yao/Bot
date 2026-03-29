import os
import asyncio
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from state import engine

BOT_TASK = None


# ================= SAFE HELPERS =================

def ensure_list(x):
    return x if isinstance(x, list) else []

def ensure_dict(x):
    return x if isinstance(x, dict) else {}

def ensure_float(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default

def ensure_int(x, default=0):
    try:
        return int(x)
    except Exception:
        return default

def ensure_str(x, default=""):
    try:
        return str(x)
    except Exception:
        return default


# ================= INIT =================

def init_engine():
    engine.running = bool(getattr(engine, "running", True))
    engine.mode = ensure_str(getattr(engine, "mode", "PAPER"), "PAPER")

    engine.positions = ensure_list(getattr(engine, "positions", []))
    engine.logs = ensure_list(getattr(engine, "logs", []))
    engine.trade_history = ensure_list(getattr(engine, "trade_history", []))

    raw_stats = ensure_dict(getattr(engine, "stats", {}))
    engine.stats = {
        "signals": ensure_int(raw_stats.get("signals", 0)),
        "buys": ensure_int(raw_stats.get("buys", 0)),
        "sells": ensure_int(raw_stats.get("sells", 0)),
        "errors": ensure_int(raw_stats.get("errors", 0)),
    }

    engine.last_trade = ensure_str(getattr(engine, "last_trade", ""))
    engine.last_signal = ensure_str(getattr(engine, "last_signal", ""))

    engine.capital = ensure_float(getattr(engine, "capital", 1.0), 1.0)
    engine.sol_balance = ensure_float(getattr(engine, "sol_balance", 1.0), 1.0)

    engine.bot_ok = bool(getattr(engine, "bot_ok", True))
    engine.bot_error = ensure_str(getattr(engine, "bot_error", ""))

    raw_engine_stats = ensure_dict(getattr(engine, "engine_stats", {}))
    engine.engine_stats = {
        "stable": {
            "pnl": ensure_float(ensure_dict(raw_engine_stats.get("stable", {})).get("pnl", 0.0)),
            "trades": ensure_int(ensure_dict(raw_engine_stats.get("stable", {})).get("trades", 0)),
            "wins": ensure_int(ensure_dict(raw_engine_stats.get("stable", {})).get("wins", 0)),
        },
        "degen": {
            "pnl": ensure_float(ensure_dict(raw_engine_stats.get("degen", {})).get("pnl", 0.0)),
            "trades": ensure_int(ensure_dict(raw_engine_stats.get("degen", {})).get("trades", 0)),
            "wins": ensure_int(ensure_dict(raw_engine_stats.get("degen", {})).get("wins", 0)),
        },
        "sniper": {
            "pnl": ensure_float(ensure_dict(raw_engine_stats.get("sniper", {})).get("pnl", 0.0)),
            "trades": ensure_int(ensure_dict(raw_engine_stats.get("sniper", {})).get("trades", 0)),
            "wins": ensure_int(ensure_dict(raw_engine_stats.get("sniper", {})).get("wins", 0)),
        },
    }

    raw_allocator = ensure_dict(getattr(engine, "engine_allocator", {}))
    engine.engine_allocator = {
        "stable": ensure_float(raw_allocator.get("stable", 0.4), 0.4),
        "degen": ensure_float(raw_allocator.get("degen", 0.4), 0.4),
        "sniper": ensure_float(raw_allocator.get("sniper", 0.2), 0.2),
    }

    engine.candidate_count = ensure_int(getattr(engine, "candidate_count", 0), 0)

    engine.logs = [ensure_str(x) for x in engine.logs][-200:]
    engine.trade_history = ensure_list(engine.trade_history)[-300:]
    engine.positions = [p for p in engine.positions if isinstance(p, dict)][:50]


def log(msg: str):
    init_engine()
    engine.logs.append(ensure_str(msg))
    engine.logs = engine.logs[-200:]
    print(f"[APP] {msg}")


# ================= BOT =================

async def start_bot():
    global BOT_TASK
    init_engine()

    if BOT_TASK is not None and not BOT_TASK.done():
        return

    try:
        from bot import bot_loop

        BOT_TASK = asyncio.create_task(bot_loop(), name="bot_loop_task")
        engine.bot_ok = True
        engine.bot_error = ""

        recent_logs = ensure_list(engine.logs)[-10:]
        if not any("BOT_STARTED" in ensure_str(x) for x in recent_logs):
            log("BOT_STARTED")

    except Exception as e:
        engine.bot_ok = False
        engine.bot_error = ensure_str(e)
        engine.stats["errors"] = ensure_int(engine.stats.get("errors", 0)) + 1
        log(f"BOT_START_ERROR {e}")
        log(traceback.format_exc()[:1000])


async def stop_bot():
    global BOT_TASK

    if BOT_TASK is None:
        return

    if not BOT_TASK.done():
        BOT_TASK.cancel()
        try:
            await BOT_TASK
        except asyncio.CancelledError:
            log("BOT_STOPPED")
        except Exception as e:
            log(f"BOT_STOP_ERROR {e}")

    BOT_TASK = None


# ================= LIFESPAN =================

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_engine()
    await start_bot()
    try:
        yield
    finally:
        await stop_bot()


app = FastAPI(lifespan=lifespan)


# ================= HELPERS =================

def normalize_position(p: dict):
    if not isinstance(p, dict):
        return {
            "token": "",
            "amount": 0.0,
            "entry_price": 0.0,
            "last_price": 0.0,
            "peak_price": 0.0,
            "pnl_pct": 0.0,
            "engine": "",
            "alpha": 0.0,
        }

    entry = ensure_float(p.get("entry_price", p.get("entry", 0)), 0.0)
    last = ensure_float(p.get("last_price", entry), entry)
    peak = ensure_float(p.get("peak_price", p.get("peak", entry)), entry)

    pnl_pct = p.get("pnl_pct")
    if pnl_pct is None:
        pnl_pct = ((last - entry) / entry) if entry > 0 else 0.0
    pnl_pct = ensure_float(pnl_pct, 0.0)

    return {
        "token": ensure_str(p.get("token", "")),
        "amount": ensure_float(p.get("amount", 0), 0.0),
        "entry_price": entry,
        "last_price": last,
        "peak_price": peak,
        "pnl_pct": pnl_pct,
        "engine": ensure_str(p.get("engine", "")),
        "alpha": ensure_float(p.get("alpha", 0), 0.0),
    }


def normalize_trade(x):
    if not isinstance(x, dict):
        return {"raw": ensure_str(x)}

    return {
        "token": ensure_str(x.get("token", x.get("mint", ""))),
        "entry_price": ensure_float(x.get("entry_price", 0.0), 0.0),
        "exit_price": ensure_float(x.get("exit_price", 0.0), 0.0),
        "pnl_pct": ensure_float(x.get("pnl_pct", 0.0), 0.0),
        "engine": ensure_str(x.get("engine", "")),
        "alpha": ensure_float(x.get("alpha", 0.0), 0.0),
        "side": ensure_str(x.get("side", "TRADE")),
        "ts": x.get("ts"),
        "raw": x,
    }


# ================= API =================

@app.get("/health")
def health():
    init_engine()
    return {
        "ok": True,
        "bot_ok": engine.bot_ok,
        "bot_error": engine.bot_error,
        "task_running": BOT_TASK is not None and not BOT_TASK.done(),
    }


@app.get("/data")
def data():
    init_engine()

    positions = [normalize_position(p) for p in ensure_list(engine.positions)]
    trade_history = [normalize_trade(x) for x in ensure_list(engine.trade_history)[-100:]]

    return {
        "running": bool(engine.running),
        "mode": ensure_str(engine.mode),
        "sol_balance": ensure_float(engine.sol_balance, 0.0),
        "capital": ensure_float(engine.capital, 0.0),
        "last_signal": ensure_str(engine.last_signal),
        "last_trade": ensure_str(engine.last_trade),
        "positions": positions,
        "logs": [ensure_str(x) for x in ensure_list(engine.logs)[-80:]],
        "stats": dict(ensure_dict(engine.stats)),
        "trade_history": trade_history,
        "bot_ok": bool(engine.bot_ok),
        "bot_error": ensure_str(engine.bot_error),
        "engine_stats": dict(ensure_dict(engine.engine_stats)),
        "engine_allocator": dict(ensure_dict(engine.engine_allocator)),
        "candidate_count": ensure_int(engine.candidate_count, 0),
    }


@app.post("/restart")
async def restart_bot():
    init_engine()
    await stop_bot()
    await start_bot()
    return {
        "ok": True,
        "bot_ok": engine.bot_ok,
        "bot_error": engine.bot_error,
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
<title>Quant Dashboard v1302</title>
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
button {
  background: #1f6feb;
  color: white;
  border: 0;
  border-radius: 8px;
  padding: 10px 14px;
  cursor: pointer;
}
button:hover {
  opacity: 0.92;
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
  <h2>Quant Dashboard v1302</h2>
  <div style="margin-bottom: 12px;">
    <button onclick="restartBot()">Restart Bot</button>
  </div>
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

async function restartBot() {
  try {
    await fetch('/restart', { method: 'POST' });
    setTimeout(load, 1000);
  } catch (e) {
    console.error(e);
  }
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
        <b>${x.side || 'TRADE'}</b> ${(x.token || '').slice(0, 12)}
        <pre>${JSON.stringify(x.raw || x, null, 2)}</pre>
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
