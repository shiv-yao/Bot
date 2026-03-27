import os
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from state import engine

BOT_STATUS = {"ok": False, "error": ""}


async def start_bot():
    try:
        from bot import bot_loop
        asyncio.create_task(bot_loop())
        BOT_STATUS["ok"] = True
        BOT_STATUS["error"] = ""
    except Exception as e:
        BOT_STATUS["ok"] = False
        BOT_STATUS["error"] = str(e)
        engine.log(f"BOT START ERROR: {e}")


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
        "positions": list(engine.positions),
        "logs": list(engine.logs)[-50:],
        "stats": dict(engine.stats),
        "trade_history": list(engine.trade_history)[-100:],
        "bot_ok": engine.bot_ok,
        "bot_error": engine.bot_error,
    })


@app.get("/", response_class=HTMLResponse)
def home():
    return """
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Quant Dashboard</title>
  <style>
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, sans-serif;
      background: #0b1020;
      color: #e5e7eb;
      padding: 20px;
    }
    .wrap {
      max-width: 1200px;
      margin: 0 auto;
    }
    .title {
      font-size: 28px;
      font-weight: 700;
      margin-bottom: 18px;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 14px;
    }
    .card {
      background: #121a2f;
      border: 1px solid #1f2a44;
      border-radius: 14px;
      padding: 16px;
      overflow: auto;
    }
    .full { grid-column: 1 / -1; }
    .two { grid-column: span 2; }
    .label {
      color: #93a3b8;
      font-size: 13px;
      margin-bottom: 8px;
    }
    .value {
      font-size: 24px;
      font-weight: 700;
    }
    ul { margin: 0; padding-left: 18px; }
    li { margin-bottom: 6px; }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
    }
    th, td {
      border-bottom: 1px solid #22304f;
      text-align: left;
      padding: 8px 6px;
      vertical-align: top;
    }
    @media (max-width: 1000px) {
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
    <div class="title">⚔️ Quant Dashboard</div>

    <div class="grid">
      <div class="card">
        <div class="label">模式</div>
        <div class="value" id="mode">-</div>
      </div>
      <div class="card">
        <div class="label">SOL 餘額</div>
        <div class="value" id="sol_balance">0</div>
      </div>
      <div class="card">
        <div class="label">資金</div>
        <div class="value" id="capital">0</div>
      </div>
      <div class="card">
        <div class="label">最後交易</div>
        <div class="value" id="last_trade" style="font-size:16px;">-</div>
      </div>

      <div class="card">
        <div class="label">訊號數</div>
        <div class="value" id="signals">0</div>
      </div>
      <div class="card">
        <div class="label">買入數</div>
        <div class="value" id="buys">0</div>
      </div>
      <div class="card">
        <div class="label">賣出數</div>
        <div class="value" id="sells">0</div>
      </div>
      <div class="card">
        <div class="label">錯誤數</div>
        <div class="value" id="errors">0</div>
      </div>

      <div class="card full">
        <div class="label">最新訊號</div>
        <div id="last_signal">-</div>
      </div>

      <div class="card two">
        <div class="label">持倉</div>
        <div id="positions">無持倉</div>
      </div>

      <div class="card two">
        <div class="label">Logs</div>
        <ul id="logs"></ul>
      </div>

      <div class="card full">
        <div class="label">Trade History</div>
        <div id="history">暫無資料</div>
      </div>
    </div>
  </div>

  <script>
    async function load() {
      const res = await fetch('/data');
      const d = await res.json();

      document.getElementById('mode').textContent = d.mode || '-';
      document.getElementById('sol_balance').textContent = Number(d.sol_balance || 0).toFixed(6);
      document.getElementById('capital').textContent = Number(d.capital || 0).toFixed(6);
      document.getElementById('last_trade').textContent = d.last_trade || '-';
      document.getElementById('last_signal').textContent = d.last_signal || '-';
      document.getElementById('signals').textContent = d.stats?.signals ?? 0;
      document.getElementById('buys').textContent = d.stats?.buys ?? 0;
      document.getElementById('sells').textContent = d.stats?.sells ?? 0;
      document.getElementById('errors').textContent = d.stats?.errors ?? 0;

      const pos = document.getElementById('positions');
      if (!d.positions || d.positions.length === 0) {
        pos.textContent = '無持倉';
      } else {
        pos.innerHTML = '<ul>' + d.positions.map(
          p => `<li>
            ${p.token || '-'} |
            amount=${p.amount ?? 0} |
            entry=${p.entry_price ?? 0} |
            last=${p.last_price ?? 0} |
            peak=${p.peak_price ?? 0} |
            pnl=${((p.pnl_pct ?? 0) * 100).toFixed(2)}%
          </li>`
        ).join('') + '</ul>';
      }

      const logs = document.getElementById('logs');
      logs.innerHTML = '';
      (d.logs || []).slice().reverse().forEach(line => {
        const li = document.createElement('li');
        li.textContent = line;
        logs.appendChild(li);
      });

      const hist = document.getElementById('history');
      const rows = d.trade_history || [];
      if (rows.length === 0) {
        hist.textContent = '暫無資料';
      } else {
        hist.innerHTML = `
          <table>
            <thead>
              <tr>
                <th>Side</th>
                <th>Mint</th>
                <th>Result</th>
              </tr>
            </thead>
            <tbody>
              ${rows.map(r => `
                <tr>
                  <td>${r.side ?? ''}</td>
                  <td>${(r.mint ?? '').slice(0, 12)}</td>
                  <td><pre style="white-space:pre-wrap;">${JSON.stringify(r.result ?? {}, null, 2)}</pre></td>
                </tr>
              `).join('')}
            </tbody>
          </table>
        `;
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
