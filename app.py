import os
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from state import engine

BOT_TASK = None


# ================= INIT =================

def init_engine():
    engine.running = True
    engine.mode = getattr(engine, "mode", "PAPER")

    if not hasattr(engine, "positions") or not isinstance(engine.positions, list):
        engine.positions = []

    if not hasattr(engine, "logs"):
        engine.logs = []

    if not isinstance(engine.logs, list):
        try:
            engine.logs = list(engine.logs)
        except Exception:
            engine.logs = []

    if not hasattr(engine, "trade_history") or not isinstance(engine.trade_history, list):
        engine.trade_history = []

    if not hasattr(engine, "stats") or not isinstance(engine.stats, dict):
        engine.stats = {
            "signals": 0,
            "buys": 0,
            "sells": 0,
            "errors": 0,
            "adds": 0,
        }

    if not hasattr(engine, "engine_stats") or not isinstance(engine.engine_stats, dict):
        engine.engine_stats = {
            "stable": {"pnl": 0.0, "trades": 0, "wins": 0},
            "degen": {"pnl": 0.0, "trades": 0, "wins": 0},
            "sniper": {"pnl": 0.0, "trades": 0, "wins": 0},
        }

    if not hasattr(engine, "engine_allocator") or not isinstance(engine.engine_allocator, dict):
        engine.engine_allocator = {
            "stable": 0.4,
            "degen": 0.4,
            "sniper": 0.2,
        }

    if not hasattr(engine, "capital"):
        engine.capital = 30.0

    if not hasattr(engine, "sol_balance"):
        engine.sol_balance = 30.0

    if not hasattr(engine, "candidate_count"):
        engine.candidate_count = 0

    if not hasattr(engine, "last_trade"):
        engine.last_trade = ""

    if not hasattr(engine, "last_signal"):
        engine.last_signal = ""

    if not hasattr(engine, "bot_ok"):
        engine.bot_ok = True

    if not hasattr(engine, "bot_error"):
        engine.bot_error = ""


def env_status():
    real_trading = os.environ.get("REAL_TRADING", "false").lower() == "true"
    private_key_ok = bool(os.environ.get("PRIVATE_KEY_JSON", "").strip())
    jup_key_ok = bool(os.environ.get("JUP_API_KEY", "").strip())
    rpc_http = os.environ.get("SOLANA_RPC_HTTP", "https://api.mainnet-beta.solana.com")
    rpc_ws = os.environ.get("SOLANA_RPC_WS", "wss://api.mainnet-beta.solana.com")
    use_jito = os.environ.get("USE_JITO", "false").lower() == "true"
    jito_url = os.environ.get("JITO_BUNDLE_URL", "")

    return {
        "real_trading": real_trading,
        "private_key_ok": private_key_ok,
        "jup_api_key_ok": jup_key_ok,
        "rpc_http": rpc_http,
        "rpc_ws": rpc_ws,
        "use_jito": use_jito,
        "jito_bundle_url_set": bool(jito_url),
        "effective_mode": "REAL" if real_trading and private_key_ok else "PAPER",
    }


# ================= BOT =================

async def start_bot():
    global BOT_TASK

    if BOT_TASK and not BOT_TASK.done():
        return

    try:
        from bot import bot_loop
        BOT_TASK = asyncio.create_task(bot_loop())
        engine.bot_ok = True
        engine.bot_error = ""
        engine.logs.append("BOT_STARTED")
        engine.logs = engine.logs[-500:]
    except Exception as e:
        engine.bot_ok = False
        engine.bot_error = str(e)
        engine.logs.append(f"BOT_ERROR {e}")
        engine.logs = engine.logs[-500:]


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
        except Exception as e:
            engine.logs.append(f"BOT_STOP_ERR {e}")
            engine.logs = engine.logs[-500:]

    BOT_TASK = None


async def monitor_bot():
    global BOT_TASK

    while True:
        try:
            if BOT_TASK is None or BOT_TASK.done():
                engine.logs.append("BOT_RESTART")
                engine.logs = engine.logs[-500:]
                await start_bot()
        except Exception as e:
            engine.logs.append(f"MONITOR_ERR {e}")
            engine.logs = engine.logs[-500:]

        await asyncio.sleep(5)


# ================= LIFESPAN =================

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_engine()
    await start_bot()
    asyncio.create_task(monitor_bot())
    yield


app = FastAPI(lifespan=lifespan)


# ================= API =================

@app.get("/health")
def health():
    env = env_status()
    return {
        "ok": True,
        "bot_ok": engine.bot_ok,
        "bot_error": engine.bot_error,
        "running": engine.running,
        "mode": engine.mode,
        "env": env,
    }


@app.get("/data")
def data():
    try:
        snapshot = engine.snapshot()
        snapshot["env"] = env_status()
        snapshot["bot_task_alive"] = BOT_TASK is not None and not BOT_TASK.done()

        snapshot["positions"] = snapshot.get("positions") or []
        snapshot["logs"] = snapshot.get("logs") or []
        snapshot["trade_history"] = snapshot.get("trade_history") or []
        snapshot["stats"] = snapshot.get("stats") or {}
        snapshot["engine_stats"] = snapshot.get("engine_stats") or {}
        snapshot["engine_allocator"] = snapshot.get("engine_allocator") or {}

        return snapshot
    except Exception as e:
        return JSONResponse({
            "error": str(e),
            "fallback": True
        })


@app.get("/restart")
async def restart():
    await stop_bot()
    await start_bot()
    return {"status": "restarted"}


@app.get("/kill")
async def kill():
    await stop_bot()
    engine.running = False
    return {"status": "stopped"}


# ================= SIMPLE DASHBOARD =================

@app.get("/", response_class=HTMLResponse)
def home():
    return """
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>v1314 Dashboard</title>
  <style>
    body {
      background: #0b1020;
      color: #fff;
      font-family: Arial, sans-serif;
      margin: 0;
      padding: 18px;
    }
    .wrap {
      max-width: 1200px;
      margin: 0 auto;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 12px;
    }
    .card {
      background: #131b33;
      border-radius: 12px;
      padding: 14px;
      overflow: auto;
    }
    .full { grid-column: 1 / -1; }
    .two { grid-column: span 2; }
    .label {
      color: #9fb0d1;
      font-size: 12px;
      margin-bottom: 6px;
    }
    .value {
      font-size: 18px;
      font-weight: 700;
    }
    .small {
      font-size: 12px;
      color: #b9c4df;
    }
    button {
      background: #2d6cdf;
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
      font-size: 12px;
      margin: 0;
    }
    @media (max-width: 900px) {
      .grid { grid-template-columns: 1fr 1fr; }
      .two { grid-column: span 2; }
    }
    @media (max-width: 560px) {
      .grid { grid-template-columns: 1fr; }
      .two, .full { grid-column: auto; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <h2>v1314 Dashboard</h2>
    <div style="margin-bottom:12px;">
      <button onclick="restartBot()">Restart Bot</button>
      <button onclick="killBot()">Kill Bot</button>
      <button onclick="load()">Refresh</button>
    </div>
    <div id="main"></div>
  </div>

  <script>
    async function restartBot() {
      await fetch('/restart');
      setTimeout(load, 1000);
    }

    async function killBot() {
      await fetch('/kill');
      setTimeout(load, 1000);
    }

    function renderEngineStats(es) {
      const keys = ["stable", "degen", "sniper"];
      return keys.map(k => {
        const v = es?.[k] || {};
        return `
          <div style="margin-bottom:8px">
            <b>${k}</b><br>
            pnl=${Number(v.pnl || 0).toFixed(4)} |
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
        return `<div style="margin-bottom:8px"><b>${k}</b>: ${Number(v).toFixed(3)}</div>`;
      }).join("");
    }

    async function load() {
      const d = await fetch('/data').then(r => r.json());

      const positionsHtml = (d.positions || []).length === 0
        ? "No positions"
        : (d.positions || []).map(p => `
          <div style="margin-bottom:10px;">
            <b>${(p.token || '').slice(0, 12)}</b><br>
            eng=${p.engine || '-'}<br>
            amount=${Number(p.amount || 0).toFixed(4)}<br>
            entry=${Number(p.entry_price || 0).toExponential(4)}<br>
            last=${Number(p.last_price || 0).toExponential(4)}<br>
            peak=${Number(p.peak_price || 0).toExponential(4)}<br>
            pnl=${((p.pnl_pct || 0) * 100).toFixed(2)}%<br>
            mode=${p.trade_mode || '-'}<br>
            sig=${p.entry_signature || '-'}
          </div>
        `).join("");

      const logsHtml = (d.logs || []).slice().reverse().map(x => `<div>${x}</div>`).join("");

      const tradesHtml = (d.trade_history || []).length === 0
        ? "No trade history"
        : (d.trade_history || []).slice().reverse().map(t => `
          <div style="margin-bottom:10px;">
            <b>${(t.token || '').slice(0, 12)}</b><br>
            engine=${t.engine || '-'} |
            pnl=${((t.pnl_pct || 0) * 100).toFixed(2)}%<br>
            entry_sig=${t.entry_signature || '-'}<br>
            exit_sig=${t.exit_signature || '-'}
          </div>
        `).join("");

      const env = d.env || {};

      document.getElementById('main').innerHTML = `
        <div class="grid">
          <div class="card">
            <div class="label">Mode</div>
            <div class="value">${env.effective_mode || d.mode || '-'}</div>
          </div>

          <div class="card">
            <div class="label">Bot</div>
            <div class="value">${d.bot_ok ? 'OK' : 'ERROR'}</div>
          </div>

          <div class="card">
            <div class="label">Capital</div>
            <div class="value">${Number(d.capital || 0).toFixed(4)}</div>
          </div>

          <div class="card">
            <div class="label">Candidates</div>
            <div class="value">${d.candidate_count ?? 0}</div>
          </div>

          <div class="card">
            <div class="label">Signals</div>
            <div class="value">${d.stats?.signals ?? 0}</div>
          </div>

          <div class="card">
            <div class="label">Buys</div>
            <div class="value">${d.stats?.buys ?? 0}</div>
          </div>

          <div class="card">
            <div class="label">Sells</div>
            <div class="value">${d.stats?.sells ?? 0}</div>
          </div>

          <div class="card">
            <div class="label">Errors</div>
            <div class="value">${d.stats?.errors ?? 0}</div>
          </div>

          <div class="card two">
            <div class="label">Env Status</div>
            <div class="small">
              REAL_TRADING=${env.real_trading}<br>
              PRIVATE_KEY_OK=${env.private_key_ok}<br>
              JUP_API_KEY_OK=${env.jup_api_key_ok}<br>
              USE_JITO=${env.use_jito}<br>
              JITO_URL_SET=${env.jito_bundle_url_set}
            </div>
          </div>

          <div class="card two">
            <div class="label">Last Signal</div>
            <div class="small">${d.last_signal || '-'}</div>
          </div>

          <div class="card two">
            <div class="label">Engine Allocator</div>
            <div class="small">${renderAllocator(d.engine_allocator)}</div>
          </div>

          <div class="card two">
            <div class="label">Engine Stats</div>
            <div class="small">${renderEngineStats(d.engine_stats)}</div>
          </div>

          <div class="card two">
            <div class="label">Positions</div>
            <div class="small">${positionsHtml}</div>
          </div>

          <div class="card two">
            <div class="label">Logs</div>
            <div class="small">${logsHtml || 'No logs'}</div>
          </div>

          <div class="card full">
            <div class="label">Trade History</div>
            <div class="small">${tradesHtml}</div>
          </div>

          <div class="card full">
            <div class="label">Bot Error</div>
            <pre>${d.bot_error || '-'}</pre>
          </div>
        </div>
      `;
    }

    load();
    setInterval(load, 2500);
  </script>
</body>
</html>
    """


# ================= RUN =================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000)),
        log_level="info"
    )
