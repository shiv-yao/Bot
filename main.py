import asyncio, random, time, aiohttp, base64, os
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
    "flow_history": [],
    "realized_pnl": 0.0,
    "loss_streak": 0,

    "errors": 0,
    "last_error": None,

    # 🔥 debug
    "last_quote": None,
    "last_swap": None,

    "kill": False,
    "last_heartbeat": time.time(),
    "bot_version": "v47_hardened"
}

# ================= SAFE HTTP =================

async def safe_get(url):
    try:
        async with SESSION.get(url, timeout=5) as res:
            text = await res.text()

            if res.status != 200:
                STATE["last_error"] = f"GET {res.status}: {text[:100]}"
                return None

            if not text.strip():
                STATE["last_error"] = "GET empty"
                return None

            try:
                return await res.json()
            except:
                STATE["last_error"] = f"GET non-json: {text[:100]}"
                return None

    except Exception as e:
        STATE["errors"] += 1
        STATE["last_error"] = str(e)
        return None


async def safe_post(url, data):
    try:
        async with SESSION.post(url, json=data, timeout=5) as res:
            text = await res.text()

            if res.status != 200:
                STATE["last_error"] = f"POST {res.status}: {text[:100]}"
                return None

            if not text.strip():
                STATE["last_error"] = "POST empty"
                return None

            try:
                return await res.json()
            except:
                STATE["last_error"] = f"POST non-json: {text[:100]}"
                return None

    except Exception as e:
        STATE["errors"] += 1
        STATE["last_error"] = str(e)
        return None

# ================= FLOW =================

async def update_flow():
    flow = random.uniform(0,1)
    STATE["flow_history"].append(flow)
    if len(STATE["flow_history"]) > 20:
        STATE["flow_history"].pop(0)

# ================= ALPHA =================

async def compute_alpha():
    return random.uniform(40,120)

# ================= JUP =================

async def get_quote(amount, slippage):
    return await safe_get(
        f"{JUP_API}/v6/quote"
        f"?inputMint=So11111111111111111111111111111111111111112"
        f"&outputMint=So11111111111111111111111111111111111111112"
        f"&amount={int(amount*1e9)}"
        f"&slippageBps={slippage}"
    )

async def get_swap(route):
    return await safe_post(
        f"{JUP_API}/v6/swap",
        {
            "quoteResponse": route,
            "userPublicKey": str(keypair.pubkey()),
            "wrapAndUnwrapSol": True
        }
    )

# ================= JITO =================

async def send_bundle_multi(tx):
    bundle = {
        "jsonrpc":"2.0",
        "id":1,
        "method":"sendBundle",
        "params":[{"transactions":[tx],"encoding":"base64"}]
    }

    results = await asyncio.gather(
        *[safe_post(url, bundle) for url in JITO_ENDPOINTS]
    )

    for r in results:
        if r and "result" in r:
            return r["result"]

    return None

# ================= EXEC =================

async def execute_real(amount, alpha):
    slippage = BASE_SLIPPAGE + int(alpha)

    quote = await get_quote(amount, slippage)
    STATE["last_quote"] = quote

    if not quote or "data" not in quote or not quote["data"]:
        STATE["last_error"] = "quote fail"
        return None

    route = quote["data"][0]

    swap = await get_swap(route)
    STATE["last_swap"] = swap

    if not swap or "swapTransaction" not in swap:
        STATE["last_error"] = "swap fail"
        return None

    tx = VersionedTransaction.from_bytes(base64.b64decode(swap["swapTransaction"]))
    tx.sign([keypair])

    raw = base64.b64encode(bytes(tx)).decode()

    sig = await send_bundle_multi(raw)

    if sig:
        return float(route["outAmount"]) / float(route["inAmount"])

    STATE["last_error"] = "bundle fail"
    return None

# ================= LOOP =================

async def bot_loop():
    while True:
        try:
            if STATE["kill"]:
                await asyncio.sleep(2)
                continue

            await update_flow()

            for _ in range(5):
                if len(STATE["positions"]) >= MAX_POSITIONS:
                    break

                alpha = await compute_alpha()

                if alpha < 50:
                    continue

                # 🔥 FIX：size太小問題
                size = max(0.005, min(0.002*(1+alpha/50), MAX_POSITION_SIZE))

                price = await execute_real(size, alpha)

                if not price:
                    continue

                STATE["positions"].append({
                    "entry_price": price,
                    "alpha": alpha,
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

@app.post("/kill")
def kill():
    STATE["kill"] = True
    return {"ok": True}

@app.post("/resume")
def resume():
    STATE["kill"] = False
    return {"ok": True}
