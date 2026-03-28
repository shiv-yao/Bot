# ================= v90_FUND_SYSTEM_WITH_UI_FIXED =================

import asyncio
import random
import time
import aiohttp
import base64
import os

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

# ================= CONFIG =================

JUP_APIS = [
    "https://lite-api.jup.ag",
    "https://quote-api.jup.ag"
]

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
HELIUS_RPC = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"

RPCS = [
    HELIUS_RPC,
    "https://api.mainnet-beta.solana.com"
]

JITO_ENDPOINTS = [
    "https://mainnet.block-engine.jito.wtf/api/v1/bundles",
    "https://ny.block-engine.jito.wtf/api/v1/bundles"
]

INPUT_MINT = "So11111111111111111111111111111111111111112"
OUTPUT_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

MAX_POSITIONS = 6
MAX_POSITION_SIZE = 0.02
MIN_POSITION_SIZE = 0.01

STOP_LOSS = -0.07
TAKE_PROFIT = 0.30
TRAILING = 0.10
MAX_HOLD = 300

BASE_SLIPPAGE = 180
DAILY_STOP = -0.05

# ================= KEY =================

PRIVATE_KEY = os.getenv("PRIVATE_KEY", "").strip()
if not PRIVATE_KEY:
    raise RuntimeError("PRIVATE_KEY not set")

keypair = Keypair.from_base58_string(PRIVATE_KEY)

# ================= STATE =================

SESSION = None
bot_task = None

STATE = {
    "positions": [],
    "closed": [],
    "alpha_scores": [],
    "allocator": {
        "wallet": 0.25,
        "flow": 0.25,
        "mempool": 0.25,
        "launch": 0.25
    },
    "alpha_models": {
        "wallet": {"score": 1.0, "history": []},
        "flow": {"score": 1.0, "history": []},
        "mempool": {"score": 1.0, "history": []},
        "launch": {"score": 1.0, "history": []}
    },
    "daily_pnl": 0.0,
    "loss_streak": 0,
    "daily_trades": 0,
    "last_error": None,
    "kill": False,
    "started_at": time.time(),
    "last_heartbeat": time.time(),
}

HEADERS = {"User-Agent": "Mozilla/5.0"}

# ================= SAFE =================

async def safe_get(url: str):
    try:
        async with SESSION.get(url, timeout=8, headers=HEADERS) as r:
            if r.status != 200:
                STATE["last_error"] = f"GET status {r.status}"
                return None
            return await r.json()
    except Exception as e:
        STATE["last_error"] = f"GET error: {e}"
        return None

async def safe_post(url: str, data: dict):
    try:
        async with SESSION.post(url, json=data, timeout=8, headers=HEADERS) as r:
            if r.status != 200:
                STATE["last_error"] = f"POST status {r.status}"
                return None
            return await r.json()
    except Exception as e:
        STATE["last_error"] = f"POST error: {e}"
        return None

# ================= SIGNAL =================

def flow_signal() -> float:
    return random.uniform(0, 1) * 50

async def mempool_alpha() -> float:
    return random.uniform(0, 1) * 100

async def launch_alpha() -> float:
    return 200 if random.random() < 0.08 else 0

def wallet_alpha() -> float:
    return random.uniform(0, 1) * 80

# ================= ALPHA MODEL =================

def update_allocator() -> None:
    scores = {
        k: max(0.01, v["score"])
        for k, v in STATE["alpha_models"].items()
    }
    total = sum(scores.values()) or 1.0
    STATE["allocator"] = {k: v / total for k, v in scores.items()}

def update_alpha_model(pnl: float, sources: list[str]) -> None:
    for s in sources:
        m = STATE["alpha_models"][s]
        m["history"].append(pnl)
        if len(m["history"]) > 50:
            m["history"].pop(0)

        h = m["history"]
        win = sum(1 for x in h if x > 0) / len(h)
        avg = sum(h) / len(h)
        m["score"] = max(0.1, win * avg * 10)

    update_allocator()

# ================= ALPHA =================

async def compute_alpha() -> tuple[float, list[str]]:
    wallet = wallet_alpha()
    flow = flow_signal()
    mem = await mempool_alpha()
    launch = await launch_alpha()

    models = STATE["alpha_models"]

    alpha = (
        wallet * models["wallet"]["score"]
        + flow * models["flow"]["score"]
        + mem * models["mempool"]["score"]
        + launch * models["launch"]["score"]
    )

    sources = ["wallet", "flow", "mempool", "launch"]
    STATE["alpha_scores"].append(alpha)

    if len(STATE["alpha_scores"]) > 300:
        STATE["alpha_scores"] = STATE["alpha_scores"][-300:]

    return alpha, sources

# ================= SIZE =================

def get_size(alpha: float) -> float:
    size = 0.004 * (1 + alpha / 120)

    if STATE["loss_streak"] >= 2:
        size *= 0.5

    return max(MIN_POSITION_SIZE, min(size, MAX_POSITION_SIZE))

# ================= EXEC =================

async def get_quote(amount: float, slippage: int):
    for api in JUP_APIS:
        url = (
            f"{api}/v6/quote"
            f"?inputMint={INPUT_MINT}"
            f"&outputMint={OUTPUT_MINT}"
            f"&amount={int(amount * 1e9)}"
            f"&slippageBps={slippage}"
        )
        r = await safe_get(url)
        if r and "data" in r and r["data"]:
            return r
    return None

async def get_swap(route: dict):
    return await safe_post(
        f"{JUP_APIS[0]}/v6/swap",
        {
            "quoteResponse": route,
            "userPublicKey": str(keypair.pubkey())
        }
    )

async def send_bundle(raw: str):
    bundle = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "sendBundle",
        "params": [{"transactions": [raw], "encoding": "base64"}]
    }

    res = await asyncio.gather(*[safe_post(u, bundle) for u in JITO_ENDPOINTS])

    for r in res:
        if r and "result" in r:
            return r["result"]

    return None

async def execute_trade(size: float, alpha: float):
    for _ in range(4):
        slippage = BASE_SLIPPAGE + min(int(alpha), 200)

        q = await get_quote(size, slippage)
        if not q:
            continue

        route = q["data"][0]

        swap = await get_swap(route)
        if not swap or "swapTransaction" not in swap:
            continue

        try:
            tx = VersionedTransaction.from_bytes(
                base64.b64decode(swap["swapTransaction"])
            )
            tx.sign([keypair])
            raw = base64.b64encode(bytes(tx)).decode()
        except Exception as e:
            STATE["last_error"] = f"sign error: {e}"
            continue

        sig = await send_bundle(raw)
        if not sig:
            continue

        try:
            price = float(route["outAmount"]) / float(route["inAmount"])
            qty = size / price
            return price, qty
        except Exception as e:
            STATE["last_error"] = f"price parse error: {e}"

    return None, None

# ================= MONITOR =================

async def monitor_positions():
    new_positions = []

    for p in STATE["positions"]:
        price = p["entry"] * random.uniform(0.7, 1.6)
        pnl = (price - p["entry"]) * p["qty"]
        pnl_pct = pnl / max((p["entry"] * p["qty"]), 1e-9)

        p["mark_price"] = price
        p["pnl"] = pnl
        p["pnl_pct"] = pnl_pct
        p["peak_pnl_pct"] = max(p.get("peak_pnl_pct", 0.0), pnl_pct)

        close = False

        if pnl_pct < STOP_LOSS:
            close = True
        if pnl_pct > TAKE_PROFIT:
            close = True
        if p["peak_pnl_pct"] - pnl_pct > TRAILING:
            close = True
        if time.time() - p["time"] > MAX_HOLD:
            close = True

        if close:
            update_alpha_model(pnl, p["sources"])

            if pnl > 0:
                STATE["loss_streak"] = 0
            else:
                STATE["loss_streak"] += 1

            STATE["daily_pnl"] += pnl

            closed_pos = {
                **p,
                "exit_price": price,
                "closed_at": time.time(),
            }
            STATE["closed"].append(closed_pos)

            if len(STATE["closed"]) > 500:
                STATE["closed"] = STATE["closed"][-500:]

            continue

        new_positions.append(p)

    STATE["positions"] = new_positions

# ================= LOOP =================

async def bot_loop():
    while True:
        try:
            STATE["last_heartbeat"] = time.time()

            if STATE["kill"]:
                await asyncio.sleep(2)
                continue

            await monitor_positions()

            if STATE["daily_pnl"] < DAILY_STOP:
                await asyncio.sleep(5)
                continue

            for _ in range(8):
                if len(STATE["positions"]) >= MAX_POSITIONS:
                    break

                alpha, sources = await compute_alpha()

                if alpha < 40:
                    continue

                size = get_size(alpha)
                price, qty = await execute_trade(size, alpha)

                if not price:
                    continue

                STATE["positions"].append({
                    "id": f"pos_{int(time.time()*1000)}_{random.randint(1000,9999)}",
                    "entry": price,
                    "qty": qty,
                    "sources": sources,
                    "alpha": alpha,
                    "time": time.time(),
                    "mark_price": price,
                    "pnl": 0.0,
                    "pnl_pct": 0.0,
                    "peak_pnl_pct": 0.0,
                })

                STATE["daily_trades"] += 1

        except Exception as e:
            STATE["last_error"] = str(e)

        await asyncio.sleep(1)

# ================= API =================

@asynccontextmanager
async def lifespan(app: FastAPI):
    global SESSION, bot_task
    SESSION = aiohttp.ClientSession()
    bot_task = asyncio.create_task(bot_loop())
    yield
    await SESSION.close()
    bot_task.cancel()

app = FastAPI(lifespan=lifespan)

@app.get("/")
def root():
    return {"ok": True}

@app.get("/metrics")
def metrics():
    return STATE

@app.get("/brain")
def brain():
    return {
        "alpha_models": STATE["alpha_models"],
        "pnl": STATE["daily_pnl"],
        "allocator": STATE["allocator"],
        "alpha_count": len(STATE["alpha_scores"]),
    }

@app.get("/status")
def status():
    return {
        "ok": True,
        "alive": True,
        "daily_pnl": STATE["daily_pnl"],
        "daily_trades": STATE["daily_trades"],
        "loss_streak": STATE["loss_streak"],
        "open_positions": len(STATE["positions"]),
        "closed_positions": len(STATE["closed"]),
        "last_error": STATE["last_error"],
        "kill": STATE["kill"],
        "uptime_sec": int(time.time() - STATE["started_at"]),
        "last_heartbeat": STATE["last_heartbeat"],
    }

@app.post("/kill")
def kill():
    STATE["kill"] = True
    return {"ok": True}

@app.post("/resume")
def resume():
    STATE["kill"] = False
    return {"ok": True}

# ================= UI =================

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return """
<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>V90 Fund Terminal</title>
  <style>
    :root {
      --bg: #0b0f19;
      --panel: #131722;
      --panel-2: #171c28;
      --border: #222836;
      --text: #eef2ff;
      --muted: #8b93a7;
      --green: #17b26a;
      --red: #f04438;
      --blue: #2e90fa;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
    }
    .wrap {
      max-width: 1600px;
      margin: 0 auto;
      padding: 20px;
    }
    .topbar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 20px;
      margin-bottom: 20px;
    }
    .title h1 {
      margin: 0;
      font-size: 32px;
      font-weight: 900;
    }
    .title p {
      margin: 6px 0 0 0;
      color: var(--muted);
    }
    .actions {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }
    button {
      border: none;
      border-radius: 10px;
      padding: 10px 14px;
      color: white;
      font-weight: 700;
      cursor: pointer;
    }
    .btn-blue { background: var(--blue); }
    .btn-red { background: var(--red); }
    .btn-green { background: var(--green); }

    .grid-cards {
      display: grid;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 16px;
    }
    .card, .panel {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 16px;
    }
    .card .label {
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 8px;
    }
    .card .value {
      font-size: 28px;
      font-weight: 900;
      line-height: 1.1;
      word-break: break-word;
    }
    .grid-2 {
      display: grid;
      grid-template-columns: 1.2fr 1fr;
      gap: 16px;
      margin-bottom: 16px;
    }
    .panel h3 {
      margin: 0 0 12px 0;
      font-size: 18px;
    }
    .table-wrap {
      overflow: auto;
      max-height: 380px;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    th, td {
      padding: 10px 8px;
      border-bottom: 1px solid var(--border);
      text-align: left;
      vertical-align: top;
    }
    th {
      color: var(--muted);
      position: sticky;
      top: 0;
      background: var(--panel);
    }
    .bar-list {
      display: grid;
      gap: 12px;
    }
    .bar-row {
      display: grid;
      grid-template-columns: 110px 1fr 70px;
      gap: 10px;
      align-items: center;
    }
    .bar-bg {
      width: 100%;
      height: 10px;
      background: var(--panel-2);
      border-radius: 999px;
      overflow: hidden;
      border: 1px solid var(--border);
    }
    .bar-fill {
      height: 100%;
      background: linear-gradient(90deg, var(--blue), #7c3aed);
      width: 0%;
    }
    .mono {
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .muted {
      color: var(--muted);
    }
    .footer {
      margin-top: 20px;
      color: var(--muted);
      font-size: 12px;
    }
    @media (max-width: 1200px) {
      .grid-cards { grid-template-columns: repeat(3, minmax(0, 1fr)); }
      .grid-2 { grid-template-columns: 1fr; }
    }
    @media (max-width: 700px) {
      .grid-cards { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .topbar { flex-direction: column; align-items: stretch; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="topbar">
      <div class="title">
        <h1>V90 Fund Terminal</h1>
        <p>FastAPI built-in dashboard</p>
      </div>
      <div class="actions">
        <button class="btn-blue" onclick="refreshAll()">Refresh</button>
        <button class="btn-red" onclick="killBot()">Kill</button>
        <button class="btn-green" onclick="resumeBot()">Resume</button>
      </div>
    </div>

    <div class="grid-cards">
      <div class="card"><div class="label">Daily PnL</div><div class="value" id="dailyPnl">-</div></div>
      <div class="card"><div class="label">Open Positions</div><div class="value" id="openPositions">-</div></div>
      <div class="card"><div class="label">Closed Positions</div><div class="value" id="closedPositions">-</div></div>
      <div class="card"><div class="label">Daily Trades</div><div class="value" id="dailyTrades">-</div></div>
      <div class="card"><div class="label">Loss Streak</div><div class="value" id="lossStreak">-</div></div>
      <div class="card"><div class="label">Kill Switch</div><div class="value" id="killState">-</div></div>
    </div>

    <div class="grid-2">
      <div class="panel">
        <h3>Allocator</h3>
        <div class="bar-list" id="allocatorBars"></div>
      </div>

      <div class="panel">
        <h3>Alpha Models</h3>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Model</th>
                <th>Score</th>
                <th>Samples</th>
                <th>Avg PnL</th>
                <th>Winrate</th>
              </tr>
            </thead>
            <tbody id="alphaModelsTable"></tbody>
          </table>
        </div>
      </div>
    </div>

    <div class="grid-2">
      <div class="panel">
        <h3>Open Positions</h3>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Entry</th>
                <th>Qty</th>
                <th>Alpha</th>
                <th>Sources</th>
                <th>PnL</th>
                <th>PnL %</th>
              </tr>
            </thead>
            <tbody id="positionsTable"></tbody>
          </table>
        </div>
      </div>

      <div class="panel">
        <h3>Closed Positions</h3>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Entry</th>
                <th>Exit</th>
                <th>Qty</th>
                <th>PnL</th>
                <th>Closed At</th>
              </tr>
            </thead>
            <tbody id="closedTable"></tbody>
          </table>
        </div>
      </div>
    </div>

    <div class="grid-2">
      <div class="panel">
        <h3>Alpha Curve</h3>
        <div class="mono muted" id="alphaCurveText">Loading...</div>
      </div>

      <div class="panel">
        <h3>Status</h3>
        <div class="mono" id="statusJson">Loading...</div>
      </div>
    </div>

    <div class="footer">Auto refresh every 3 seconds</div>
  </div>

  <script>
    async function getJson(path, opts = {}) {
      const res = await fetch(path, opts);
      return await res.json();
    }

    function fmt(n) {
      if (typeof n !== "number") return String(n ?? "-");
      return Number.isFinite(n) ? n.toFixed(4) : "-";
    }

    function fmtPct(n) {
      if (typeof n !== "number") return "-";
      return (n * 100).toFixed(2) + "%";
    }

    function td(v) {
      return `<td>${v ?? "-"}</td>`;
    }

    function renderAllocator(allocator) {
      const root = document.getElementById("allocatorBars");
      root.innerHTML = "";

      Object.entries(allocator || {}).forEach(([k, v]) => {
        const row = document.createElement("div");
        row.className = "bar-row";
        row.innerHTML = `
          <div>${k}</div>
          <div class="bar-bg"><div class="bar-fill" style="width:${Math.max(0, Math.min(100, v * 100))}%"></div></div>
          <div>${(v * 100).toFixed(1)}%</div>
        `;
        root.appendChild(row);
      });
    }

    function renderAlphaModels(models) {
      const body = document.getElementById("alphaModelsTable");
      body.innerHTML = "";

      Object.entries(models || {}).forEach(([name, model]) => {
        const hist = model.history || [];
        const avg = hist.length ? hist.reduce((a,b)=>a+b,0) / hist.length : 0;
        const win = hist.length ? hist.filter(x => x > 0).length / hist.length : 0;

        const tr = document.createElement("tr");
        tr.innerHTML =
          td(name) +
          td(fmt(model.score)) +
          td(hist.length) +
          td(fmt(avg)) +
          td(fmtPct(win));
        body.appendChild(tr);
      });
    }

    function renderPositions(rows) {
      const body = document.getElementById("positionsTable");
      body.innerHTML = "";

      (rows || []).forEach((r) => {
        const tr = document.createElement("tr");
        tr.innerHTML =
          td(r.id) +
          td(fmt(r.entry)) +
          td(fmt(r.qty)) +
          td(fmt(r.alpha)) +
          td((r.sources || []).join(", ")) +
          td(fmt(r.pnl)) +
          td(fmtPct(r.pnl_pct));
        body.appendChild(tr);
      });
    }

    function renderClosed(rows) {
      const body = document.getElementById("closedTable");
      body.innerHTML = "";

      (rows || []).slice(-20).reverse().forEach((r) => {
        const tr = document.createElement("tr");
        tr.innerHTML =
          td(r.id) +
          td(fmt(r.entry)) +
          td(fmt(r.exit_price)) +
          td(fmt(r.qty)) +
          td(fmt(r.pnl)) +
          td(r.closed_at ? new Date(r.closed_at * 1000).toLocaleString() : "-");
        body.appendChild(tr);
      });
    }

    function renderAlphaCurve(alphaScores) {
      const el = document.getElementById("alphaCurveText");
      const last = (alphaScores || []).slice(-40);
      el.textContent = last.length ? last.map(v => fmt(v)).join("  |  ") : "No alpha data";
    }

    function renderTop(metrics, status) {
      document.getElementById("dailyPnl").textContent = fmt(metrics.daily_pnl);
      document.getElementById("openPositions").textContent = (metrics.positions || []).length;
      document.getElementById("closedPositions").textContent = (metrics.closed || []).length;
      document.getElementById("dailyTrades").textContent = metrics.daily_trades ?? "-";
      document.getElementById("lossStreak").textContent = metrics.loss_streak ?? "-";
      document.getElementById("killState").textContent = status.kill ? "ON" : "OFF";
    }

    function renderStatus(status, metrics, brain) {
      document.getElementById("statusJson").textContent = JSON.stringify({
        status,
        last_error: metrics.last_error,
        brain_pnl: brain.pnl
      }, null, 2);
    }

    async function refreshAll() {
      try {
        const [metrics, brain, status] = await Promise.all([
          getJson("/metrics"),
          getJson("/brain"),
          getJson("/status")
        ]);

        renderTop(metrics, status);
        renderAllocator(brain.allocator || metrics.allocator || {});
        renderAlphaModels(brain.alpha_models || {});
        renderPositions(metrics.positions || []);
        renderClosed(metrics.closed || []);
        renderAlphaCurve(metrics.alpha_scores || []);
        renderStatus(status, metrics, brain);
      } catch (e) {
        document.getElementById("statusJson").textContent = "UI fetch error: " + e;
      }
    }

    async function killBot() {
      await fetch("/kill", { method: "POST" });
      await refreshAll();
    }

    async function resumeBot() {
      await fetch("/resume", { method: "POST" });
      await refreshAll();
    }

    refreshAll();
    setInterval(refreshAll, 3000);
  </script>
</body>
</html>
    ""
