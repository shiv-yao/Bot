import asyncio
import random
import time
import aiohttp
import base64
import os

from contextlib import asynccontextmanager
from fastapi import FastAPI
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

# ================= CONFIG =================

JITO_ENDPOINTS = [
    "https://mainnet.block-engine.jito.wtf/api/v1/bundles",
    "https://ny.block-engine.jito.wtf/api/v1/bundles",
]

JUP_API = "https://lite-api.jup.ag"

INPUT_MINT = "So11111111111111111111111111111111111111112"   # SOL
OUTPUT_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"  # USDC

STOP_LOSS = -0.07
DAILY_STOP = -0.06

MAX_POSITIONS = 6
MAX_POSITION_PER_ENGINE = 3
MAX_DAILY_TRADES = 40
MAX_HOLD_SECONDS = 240

MAX_POSITION_SIZE = 0.01
MIN_POSITION_SIZE = 0.01

KILL_SWITCH_LOSS_STREAK = 6
BASE_SLIPPAGE = 150

USE_REAL_EXECUTION = True

# ================= KEY =================

PRIVATE_KEY = os.getenv("PRIVATE_KEY", "").strip()
if not PRIVATE_KEY:
    raise RuntimeError("PRIVATE_KEY not set")

try:
    if PRIVATE_KEY.startswith("["):
        keypair = Keypair.from_bytes(bytes(eval(PRIVATE_KEY)))
    elif "," in PRIVATE_KEY:
        keypair = Keypair.from_bytes(bytes(int(x) for x in PRIVATE_KEY.split(",")))
    else:
        keypair = Keypair.from_base58_string(PRIVATE_KEY)
except Exception as e:
    raise RuntimeError(f"PRIVATE_KEY parse error: {e}")

# ================= STATE =================

SESSION = None

STATE = {
    "positions": [],
    "closed_trades": [],
    "trade_log": [],

    "signals": 0,
    "errors": 0,
    "last_error": None,
    "last_action": None,

    "last_quote": None,
    "last_swap": None,
    "last_bundle": None,

    "realized_pnl": 0.0,
    "daily_pnl": 0.0,
    "daily_trades": 0,
    "last_reset": time.time(),

    "loss_streak": 0,
    "regime": "chop",
    "kill": False,
    "last_heartbeat": time.time(),

    "alpha_memory": {
        "stable": [],
        "degen": [],
        "sniper": []
    },

    "engine_stats": {
        "stable": {"pnl": 0.0, "trades": 0, "wins": 0},
        "degen": {"pnl": 0.0, "trades": 0, "wins": 0},
        "sniper": {"pnl": 0.0, "trades": 0, "wins": 0},
    },

    "allocator": {
        "stable": 0.4,
        "degen": 0.4,
        "sniper": 0.2,
    },

    "bot_version": "v51_integrated_fund_brain"
}

# ================= SAFE =================

async def safe_get(url: str):
    try:
        async with SESSION.get(url, timeout=6) as res:
            text = await res.text()

            if res.status != 200:
                STATE["errors"] += 1
                STATE["last_error"] = f"GET {res.status}: {text[:180]}"
                return None

            if not text.strip():
                STATE["errors"] += 1
                STATE["last_error"] = "GET empty response"
                return None

            try:
                return await res.json()
            except Exception:
                STATE["errors"] += 1
                STATE["last_error"] = f"GET non-json: {text[:180]}"
                return None

    except Exception as e:
        STATE["errors"] += 1
        STATE["last_error"] = f"GET error: {e}"
        return None


async def safe_post(url: str, data: dict):
    try:
        async with SESSION.post(url, json=data, timeout=6) as res:
            text = await res.text()

            if res.status != 200:
                STATE["errors"] += 1
                STATE["last_error"] = f"POST {res.status}: {text[:180]}"
                return None

            if not text.strip():
                STATE["errors"] += 1
                STATE["last_error"] = "POST empty response"
                return None

            try:
                return await res.json()
            except Exception:
                STATE["errors"] += 1
                STATE["last_error"] = f"POST non-json: {text[:180]}"
                return None

    except Exception as e:
        STATE["errors"] += 1
        STATE["last_error"] = f"POST error: {e}"
        return None

# ================= REGIME =================

def detect_regime():
    pnl = STATE["daily_pnl"]
    loss = STATE["loss_streak"]

    if pnl > 0.02:
        return "bull"
    if loss >= 4:
        return "bear"
    return "chop"

# ================= WALLET / FLOW =================

async def fetch_wallets():
    # 先用模擬，之後可換 Helius / 自建 indexer
    return [
        {
            "winrate": random.uniform(0.4, 0.8),
            "pnl": random.uniform(-1, 1),
            "size": random.uniform(0, 1),
        }
        for _ in range(10)
    ]

def wallet_score(wallets):
    scores = []
    for w in wallets:
        score = w["winrate"] * 0.5 + w["pnl"] * 0.3 + w["size"] * 0.2
        scores.append(score)
    return sum(scores) / len(scores)

async def update_flow():
    wallets = await fetch_wallets()
    flow = wallet_score(wallets)
    STATE["flow_history"] = STATE.get("flow_history", [])
    STATE["flow_history"].append(flow)

    if len(STATE["flow_history"]) > 20:
        STATE["flow_history"].pop(0)

def flow_acceleration():
    hist = STATE.get("flow_history", [])
    if len(hist) < 2:
        return 0.0
    return hist[-1] - hist[-2]

def mempool_pressure():
    return random.uniform(0, 1)

def detect_launch():
    return random.random() < 0.1

# ================= ENGINE / ALPHA =================

def update_alpha_memory(engine, alpha, pnl):
    mem = STATE["alpha_memory"][engine]
    mem.append((alpha, pnl))
    if len(mem) > 100:
        mem.pop(0)

def get_alpha_edge(engine, alpha):
    mem = STATE["alpha_memory"][engine]
    if not mem:
        return 1.0

    similar = [p for a, p in mem if abs(a - alpha) < 10]
    if not similar:
        return 1.0

    avg = sum(similar) / len(similar)
    return max(0.5, min(2.0, 1 + avg * 5))

def update_allocator():
    stats = STATE["engine_stats"]
    weights = {}

    for e in ["stable", "degen", "sniper"]:
        s = stats[e]
        if s["trades"] == 0:
            weights[e] = 1.0
        else:
            winrate = s["wins"] / max(s["trades"], 1)
            weights[e] = max(0.05, (s["pnl"] + 0.001) * winrate)

    total = sum(abs(w) for w in weights.values()) + 1e-9
    weights = {k: abs(v) / total for k, v in weights.items()}

    if STATE["regime"] == "bull":
        weights["degen"] += 0.2
        weights["sniper"] += 0.1
    elif STATE["regime"] == "bear":
        weights["stable"] += 0.3

    total = sum(weights.values())
    STATE["allocator"] = {k: v / total for k, v in weights.items()}

def choose_engine(alpha):
    if alpha >= 50:
        return "sniper"
    if alpha >= 30:
        return "degen"
    return "stable"

async def compute_alpha():
    flow = STATE.get("flow_history", [0.5])[-1] if STATE.get("flow_history") else 0.5
    accel = flow_acceleration()
    mem = mempool_pressure()
    launch = detect_launch()

    base_alpha = (
        flow * 50 +
        accel * 80 +
        mem * 60 +
        (80 if launch else 0)
    )

    return max(0.0, base_alpha)

def get_size(alpha, engine):
    base = {
        "stable": 0.003,
        "degen": 0.002,
        "sniper": 0.0015,
    }[engine]

    weight = STATE["allocator"][engine]
    edge = get_alpha_edge(engine, alpha)

    size = base * weight * (1 + alpha / 50) * edge

    if STATE["loss_streak"] >= 2:
        size *= 0.5

    size = max(MIN_POSITION_SIZE, min(size, MAX_POSITION_SIZE))
    return round(size, 4)

# ================= JUP =================

async def get_quote(amount: float, slippage: int):
    url = (
        f"{JUP_API}/v6/quote"
        f"?inputMint={INPUT_MINT}"
        f"&outputMint={OUTPUT_MINT}"
        f"&amount={int(amount * 1e9)}"
        f"&slippageBps={slippage}"
    )
    return await safe_get(url)

async def get_swap(route: dict):
    return await safe_post(
        f"{JUP_API}/v6/swap",
        {
            "quoteResponse": route,
            "userPublicKey": str(keypair.pubkey()),
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
        }
    )

# ================= JITO =================

async def send_bundle_multi(tx: str):
    bundle = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "sendBundle",
        "params": [{"transactions": [tx], "encoding": "base64"}]
    }

    results = await asyncio.gather(*[safe_post(url, bundle) for url in JITO_ENDPOINTS])
    STATE["last_bundle"] = results

    for r in results:
        if r and "result" in r:
            return r["result"]

    STATE["last_error"] = "bundle fail"
    return None

# ================= EXEC =================

async def execute_real(amount: float, alpha: float):
    slippage = BASE_SLIPPAGE + int(alpha * 2)

    quote = await get_quote(amount, slippage)
    STATE["last_quote"] = quote

    if not quote:
        STATE["last_error"] = "quote: no response"
        return None

    if "data" not in quote:
        STATE["last_error"] = f"quote bad response: {quote}"
        return None

    if not quote["data"]:
        STATE["last_error"] = "quote: empty routes"
        return None

    route = quote["data"][0]

    swap = await get_swap(route)
    STATE["last_swap"] = swap

    if not swap:
        STATE["last_error"] = "swap: no response"
        return None

    if "swapTransaction" not in swap:
        STATE["last_error"] = f"swap bad response: {swap}"
        return None

    try:
        tx = VersionedTransaction.from_bytes(base64.b64decode(swap["swapTransaction"]))
        tx.sign([keypair])
        raw = base64.b64encode(bytes(tx)).decode()
    except Exception as e:
        STATE["errors"] += 1
        STATE["last_error"] = f"sign error: {e}"
        return None

    sig = await send_bundle_multi(raw)
    if not sig:
        return None

    try:
        return {
            "entry_price": float(route["outAmount"]) / float(route["inAmount"]),
            "signature": sig,
            "quote": route,
        }
    except Exception as e:
        STATE["errors"] += 1
        STATE["last_error"] = f"price parse error: {e}"
        return None

async def simulate_buy(size: float):
    price = random.uniform(0.00001, 0.00002)
    qty = size / price
    return {
        "entry_price": price,
        "qty": qty,
        "signature": "SIMULATED",
        "quote": None,
    }

async def execute_trade(size: float, alpha: float):
    if USE_REAL_EXECUTION:
        return await execute_real(size, alpha)
    return await simulate_buy(size)

# ================= MONITOR =================

async def simulate_mark_price(entry_price: float):
    return entry_price * random.uniform(0.6, 1.6)

async def monitor():
    new_positions = []

    for pos in STATE["positions"]:
        mark_price = await simulate_mark_price(pos["entry_price"])
        qty = pos["qty"]

        pnl = (mark_price - pos["entry_price"]) * qty
        pnl_pct = pnl / max(pos["entry_price"] * qty, 1e-9)

        pos["pnl_pct"] = pnl_pct
        pos["peak"] = max(pos.get("peak", 0.0), pnl_pct)

        if not pos.get("tp1") and pnl_pct > 0.10:
            pos["tp1"] = True
            pos["qty"] *= 0.5

        giveback = 0.06
        if pos["peak"] > 0.3:
            giveback = 0.10
        if pos["peak"] > 0.6:
            giveback = 0.20

        timeout = (time.time() - pos["entry_time"]) > MAX_HOLD_SECONDS

        should_close = (
            pnl_pct < STOP_LOSS
            or (pos["peak"] > 0.08 and pos["peak"] - pnl_pct > giveback)
            or timeout
        )

        if should_close:
            engine = pos["engine"]

            record = {
                **pos,
                "exit_price": mark_price,
                "pnl": pnl,
                "closed_at": time.time(),
            }

            STATE["closed_trades"].append(record)
            STATE["trade_log"].append(record)
            STATE["realized_pnl"] += pnl
            STATE["daily_pnl"] += pnl

            update_alpha_memory(engine, pos["alpha"], pnl)

            st = STATE["engine_stats"][engine]
            st["trades"] += 1
            st["pnl"] += pnl

            if pnl > 0:
                st["wins"] += 1
                STATE["loss_streak"] = 0
            else:
                STATE["loss_streak"] += 1

            continue

        new_positions.append(pos)

    STATE["positions"] = new_positions

# ================= LOOP =================

async def bot_loop():
    while True:
        try:
            now = time.time()

            if now - STATE["last_reset"] > 86400:
                STATE["daily_pnl"] = 0.0
                STATE["daily_trades"] = 0
                STATE["loss_streak"] = 0
                STATE["last_reset"] = now

            if STATE["kill"]:
                STATE["last_action"] = "manual_kill"
                await asyncio.sleep(2)
                continue

            if STATE["loss_streak"] >= KILL_SWITCH_LOSS_STREAK:
                STATE["last_action"] = "kill_switch"
                await asyncio.sleep(5)
                continue

            if STATE["daily_pnl"] < DAILY_STOP:
                STATE["last_action"] = "daily_stop"
                await asyncio.sleep(5)
                continue

            await update_flow()

            STATE["regime"] = detect_regime()
            update_allocator()
            STATE["signals"] += 1

            await monitor()

            if STATE["daily_trades"] >= MAX_DAILY_TRADES:
                STATE["last_action"] = "max_daily_trades"
                await asyncio.sleep(2)
                continue

            for _ in range(12):
                if len(STATE["positions"]) >= MAX_POSITIONS:
                    break

                alpha = await compute_alpha()
                engine = choose_engine(alpha)

                if sum(1 for p in STATE["positions"] if p["engine"] == engine) >= MAX_POSITION_PER_ENGINE:
                    continue

                if engine == "stable" and alpha < 10:
                    continue
                if engine == "degen" and alpha < 30:
                    continue
                if engine == "sniper" and alpha < 50:
                    continue

                size = get_size(alpha, engine)

                result = await execute_trade(size, alpha)
                if not result:
                    continue

                qty = result["qty"] if "qty" in result else size / result["entry_price"]

                STATE["positions"].append({
                    "token": f"TOKEN{random.randint(1,9999)}",
                    "entry_price": result["entry_price"],
                    "qty": qty,
                    "alpha": alpha,
                    "engine": engine,
                    "entry_time": time.time(),
                    "signature": result["signature"],
                    "tp1": False,
                    "peak": 0.0,
                })

                STATE["daily_trades"] += 1
                STATE["last_action"] = f"{engine}_buy"

        except Exception as e:
            STATE["errors"] += 1
            STATE["last_error"] = str(e)
            STATE["last_action"] = str(e)

        STATE["last_heartbeat"] = time.time()
        await asyncio.sleep(1)

# ================= API =================

bot_task = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot_task, SESSION

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

@app.get("/health")
def health():
    return {"status": "alive", "errors": STATE["errors"]}

@app.get("/status")
def status():
    return {
        "positions": len(STATE["positions"]),
        "pnl": STATE["realized_pnl"],
        "heartbeat": STATE["last_heartbeat"],
        "regime": STATE["regime"],
        "allocator": STATE["allocator"],
    }

@app.post("/kill")
def kill():
    STATE["kill"] = True
    return {"ok": True}

@app.post("/resume")
def resume():
    STATE["kill"] = False
    return {"ok": True}
