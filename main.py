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
    "https://ny.block-engine.jito.wtf/api/v1/bundles"
]

JUP_API = "https://lite-api.jup.ag"

MAX_POSITIONS = 5
MAX_POSITION_SIZE = 0.01

STOP_LOSS = -0.07
KILL_SWITCH = 5
BASE_SLIPPAGE = 150

INPUT_MINT = "So11111111111111111111111111111111111111112"   # SOL
OUTPUT_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"  # USDC

# ================= KEY =================

PRIVATE_KEY = os.getenv("PRIVATE_KEY", "").strip()

if not PRIVATE_KEY:
    raise RuntimeError("PRIVATE_KEY not set")

if PRIVATE_KEY.startswith("["):
    keypair = Keypair.from_bytes(bytes(eval(PRIVATE_KEY)))
elif "," in PRIVATE_KEY:
    keypair = Keypair.from_bytes(bytes(int(x) for x in PRIVATE_KEY.split(",")))
else:
    keypair = Keypair.from_base58_string(PRIVATE_KEY)

# ================= STATE =================

SESSION = None

STATE = {
    "positions": [],
    "wallet_history": [],
    "flow_history": [],
    "realized_pnl": 0.0,
    "loss_streak": 0,
    "errors": 0,
    "last_error": None,
    "last_quote": None,
    "last_swap": None,
    "last_bundle": None,
    "last_heartbeat": time.time(),
    "bot_version": "v45_onchain_intelligence_debug"
}

# ================= SAFE =================

async def safe_get(url: str):
    try:
        async with SESSION.get(url, timeout=5) as res:
            text = await res.text()

            if res.status != 200:
                STATE["errors"] += 1
                STATE["last_error"] = f"GET {res.status}: {text[:160]}"
                return None

            if not text.strip():
                STATE["errors"] += 1
                STATE["last_error"] = "GET empty response"
                return None

            try:
                return await res.json()
            except Exception:
                STATE["errors"] += 1
                STATE["last_error"] = f"GET non-json: {text[:160]}"
                return None

    except Exception as e:
        STATE["errors"] += 1
        STATE["last_error"] = f"GET error: {e}"
        return None


async def safe_post(url: str, data: dict):
    try:
        async with SESSION.post(url, json=data, timeout=5) as res:
            text = await res.text()

            if res.status != 200:
                STATE["errors"] += 1
                STATE["last_error"] = f"POST {res.status}: {text[:160]}"
                return None

            if not text.strip():
                STATE["errors"] += 1
                STATE["last_error"] = "POST empty response"
                return None

            try:
                return await res.json()
            except Exception:
                STATE["errors"] += 1
                STATE["last_error"] = f"POST non-json: {text[:160]}"
                return None

    except Exception as e:
        STATE["errors"] += 1
        STATE["last_error"] = f"POST error: {e}"
        return None

# ================= WALLET INTEL =================

async def fetch_wallets():
    wallets = []
    for _ in range(10):
        wallets.append({
            "winrate": random.uniform(0.4, 0.8),
            "pnl": random.uniform(-1, 1),
            "size": random.uniform(0, 1)
        })
    return wallets

def wallet_score(wallets):
    scores = []
    for w in wallets:
        score = w["winrate"] * 0.5 + w["pnl"] * 0.3 + w["size"] * 0.2
        scores.append(score)
    return sum(scores) / len(scores)

# ================= FLOW =================

async def update_flow():
    flow = random.uniform(0, 1)
    STATE["flow_history"].append(flow)

    if len(STATE["flow_history"]) > 20:
        STATE["flow_history"].pop(0)

def flow_acceleration():
    if len(STATE["flow_history"]) < 2:
        return 0
    return STATE["flow_history"][-1] - STATE["flow_history"][-2]

# ================= MEMPOOL =================

def mempool_pressure():
    return random.uniform(0, 1)

# ================= LAUNCH =================

def detect_launch():
    return random.random() < 0.1

# ================= ALPHA =================

async def compute_alpha():
    wallets = await fetch_wallets()
    flow = wallet_score(wallets)

    accel = flow_acceleration()
    mem = mempool_pressure()
    launch = detect_launch()

    alpha = (
        flow * 50 +
        accel * 80 +
        mem * 60 +
        (80 if launch else 0)
    )
    return alpha

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
            "wrapAndUnwrapSol": True
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

    if sig:
        try:
            return float(route["outAmount"]) / float(route["inAmount"])
        except Exception as e:
            STATE["errors"] += 1
            STATE["last_error"] = f"price parse error: {e}"
            return None

    return None

# ================= LOOP =================

async def bot_loop():
    while True:
        try:
            if STATE["loss_streak"] >= KILL_SWITCH:
                await asyncio.sleep(5)
                continue

            await update_flow()

            for _ in range(5):
                if len(STATE["positions"]) >= MAX_POSITIONS:
                    break

                alpha = await compute_alpha()

                if alpha < 50:
                    continue

                # 下限拉高，避免太小拿不到 route
                size = max(0.01, min(0.002 * (1 + alpha / 50), MAX_POSITION_SIZE))

                price = await execute_real(size, alpha)

                if not price:
                    continue

                STATE["positions"].append({
                    "token": f"TOKEN{random.randint(1,9999)}",
                    "entry_price": price,
                    "alpha": alpha,
                    "amount_sol": size,
                    "time": time.time()
                })

        except Exception as e:
            STATE["errors"] += 1
            STATE["last_error"] = str(e)

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
