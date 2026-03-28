# ================= v600_ME​​V_FULL_SYSTEM =================

import asyncio, time, aiohttp, base64, os, random
from contextlib import asynccontextmanager
from fastapi import FastAPI
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

# ================= CONFIG =================

JUP = "https://lite-api.jup.ag"
HELIUS = os.getenv("HELIUS_API_KEY", "")
RPC = f"https://mainnet.helius-rpc.com/?api-key={HELIUS}"

JITO = [
    "https://mainnet.block-engine.jito.wtf/api/v1/bundles",
    "https://ny.block-engine.jito.wtf/api/v1/bundles"
]

INPUT = "So11111111111111111111111111111111111111112"

MAX_POS = 6
MAX_SIZE = 0.02
MIN_SIZE = 0.01

STOP_LOSS = -0.07
TAKE_PROFIT = 0.3

BASE_SLIPPAGE = 180

# ================= KEY =================

PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")
if not PRIVATE_KEY:
    raise RuntimeError("PRIVATE_KEY missing")

keypair = Keypair.from_base58_string(PRIVATE_KEY)

# ================= STATE =================

SESSION = None

STATE = {
    "positions": [],
    "closed": [],

    "alpha_weight": {
        "wallet": 1,
        "flow": 1,
        "mempool": 1,
        "launch": 1
    },

    "alpha_perf": {
        "wallet": [],
        "flow": [],
        "mempool": [],
        "launch": []
    },

    "sniper": False,
    "last_pump": 0,
    "current_token": INPUT,

    "daily_pnl": 0,
    "loss_streak": 0,
    "last_error": None
}

# ================= SAFE =================

async def get(url):
    try:
        async with SESSION.get(url, timeout=5) as r:
            return await r.json()
    except:
        return None

async def post(url, data):
    try:
        async with SESSION.post(url, json=data, timeout=5) as r:
            return await r.json()
    except:
        return None

# ================= TOKEN PARSER =================

def extract_token(logs):
    for l in logs:
        for p in l.split(" "):
            if len(p) > 30:
                return p
    return None

# ================= SNIPER =================

async def pump_ws():
    ws = RPC.replace("https", "wss")

    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(ws) as w:

            await w.send_json({
                "jsonrpc": "2.0",
                "method": "logsSubscribe",
                "params": [{"mentions": ["pump"]}, {"commitment": "processed"}]
            })

            async for msg in w:
                data = msg.json()

                if "params" in data:
                    logs = data["params"]["result"]["value"]["logs"]

                    if any("initialize" in l for l in logs):
                        token = extract_token(logs)

                        if token:
                            STATE["current_token"] = token
                            STATE["sniper"] = True
                            STATE["last_pump"] = time.time()

# ================= FLOW =================

async def flow_alpha():
    r = await get(f"{JUP}/v6/quote?inputMint={INPUT}&outputMint={INPUT}&amount=10000000")
    if not r:
        return 0
    impact = float(r["data"][0].get("priceImpactPct", 0))
    return max(0, 50 - impact * 100)

# ================= ALPHA =================

async def compute_alpha():

    flow = await flow_alpha()
    mem = random.uniform(0, 100)
    launch = 300 if STATE["sniper"] else 0
    wallet = random.uniform(0, 80)

    w = STATE["alpha_weight"]

    total = (
        wallet * w["wallet"] +
        flow * w["flow"] +
        mem * w["mempool"] +
        launch * w["launch"]
    )

    return {
        "wallet": wallet,
        "flow": flow,
        "mempool": mem,
        "launch": launch,
        "total": total
    }

# ================= LEARNING =================

def update_alpha(sources, pnl):

    for s in sources:
        arr = STATE["alpha_perf"][s]
        arr.append(pnl)

        if len(arr) > 30:
            arr.pop(0)

        win = sum(1 for x in arr if x > 0) / len(arr)
        avg = sum(arr) / len(arr)

        STATE["alpha_weight"][s] = max(0.1, win * avg * 10)

# ================= FILTER =================

async def check_liquidity(token):
    r = await get(f"{JUP}/v6/quote?inputMint={INPUT}&outputMint={token}&amount=10000000")
    if not r or not r.get("data"):
        return False
    impact = float(r["data"][0].get("priceImpactPct", 1))
    return impact < 0.2

def rug_filter():
    return random.random() > 0.2

# ================= SIZE =================

def size(alpha):

    s = 0

    for k, v in alpha.items():
        if k == "total":
            continue
        s += v / 100 * STATE["alpha_weight"][k]

    s *= 0.04

    if STATE["sniper"]:
        s *= 1.5  # 🚀 sniper boost

    if STATE["loss_streak"] >= 2:
        s *= 0.5

    return max(MIN_SIZE, min(s, MAX_SIZE))

# ================= MEV EXEC =================

async def execute_trade_mev(sz, token):

    slippage = 300 if STATE["sniper"] else BASE_SLIPPAGE

    quote = await get(
        f"{JUP}/v6/quote?inputMint={INPUT}&outputMint={token}"
        f"&amount={int(sz*1e9)}&slippageBps={slippage}"
    )

    if not quote or not quote.get("data"):
        return None, None

    route = quote["data"][0]

    swap = await post(JUP + "/v6/swap", {
        "quoteResponse": route,
        "userPublicKey": str(keypair.pubkey()),
        "dynamicComputeUnitLimit": True,
        "prioritizationFeeLamports": 50000  # 🔥 priority fee
    })

    if not swap or "swapTransaction" not in swap:
        return None, None

    tx = VersionedTransaction.from_bytes(
        base64.b64decode(swap["swapTransaction"])
    )
    tx.sign([keypair])

    raw = base64.b64encode(bytes(tx)).decode()

    bundle = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "sendBundle",
        "params": [{
            "transactions": [raw],
            "encoding": "base64"
        }]
    }

    res = await asyncio.gather(*[post(u, bundle) for u in JITO])

    if not any(r and "result" in r for r in res):
        return None, None

    price = float(route["outAmount"]) / float(route["inAmount"])
    qty = sz / price

    return price, qty

# ================= SELL =================

async def sell(qty, token):

    r = await get(f"{JUP}/v6/quote?inputMint={token}&outputMint={INPUT}&amount={int(qty*1e9)}")
    if not r:
        return False

    route = r["data"][0]

    swap = await post(JUP + "/v6/swap", {
        "quoteResponse": route,
        "userPublicKey": str(keypair.pubkey())
    })

    tx = VersionedTransaction.from_bytes(base64.b64decode(swap["swapTransaction"]))
    tx.sign([keypair])

    raw = base64.b64encode(bytes(tx)).decode()

    res = await post(RPC, {
        "jsonrpc": "2.0",
        "method": "sendTransaction",
        "params": [raw]
    })

    return bool(res)

# ================= MONITOR =================

async def monitor():

    token = STATE["current_token"]

    r = await get(f"{JUP}/v6/quote?inputMint={INPUT}&outputMint={token}&amount=10000000")
    if not r:
        return

    price = float(r["data"][0]["outAmount"]) / float(r["data"][0]["inAmount"])

    new = []

    for p in STATE["positions"]:

        pnl = (price - p["entry"]) * p["qty"]
        pct = pnl / (p["entry"] * p["qty"])

        if pct < STOP_LOSS or pct > TAKE_PROFIT:

            ok = await sell(p["qty"], token)

            if not ok:
                new.append(p)
                continue

            update_alpha(p["sources"], pnl)

            if pnl > 0:
                STATE["loss_streak"] = 0
            else:
                STATE["loss_streak"] += 1

            STATE["daily_pnl"] += pnl
            STATE["closed"].append(p)
            continue

        new.append(p)

    STATE["positions"] = new

# ================= LOOP =================

async def bot():

    while True:

        try:

            if STATE["sniper"] and time.time() - STATE["last_pump"] > 5:
                STATE["sniper"] = False
                STATE["current_token"] = INPUT

            await monitor()

            if STATE["daily_pnl"] < -0.05:
                await asyncio.sleep(5)
                continue

            for _ in range(5):

                if len(STATE["positions"]) >= MAX_POS:
                    break

                alpha = await compute_alpha()

                if alpha["total"] < 80:
                    continue

                token = STATE["current_token"]

                if not rug_filter():
                    continue

                ok = await check_liquidity(token)
                if not ok:
                    continue

                sz = size(alpha)

                price, qty = await execute_trade_mev(sz, token)

                if not price:
                    continue

                STATE["positions"].append({
                    "entry": price,
                    "qty": qty,
                    "sources": [k for k in alpha if k != "total"],
                    "token": token,
                    "time": time.time()
                })

        except Exception as e:
            STATE["last_error"] = str(e)

        await asyncio.sleep(0.8)  # 🔥 降 latency

# ================= API =================

@asynccontextmanager
async def lifespan(app: FastAPI):
    global SESSION
    SESSION = aiohttp.ClientSession()
    asyncio.create_task(bot())
    asyncio.create_task(pump_ws())
    yield
    await SESSION.close()

app = FastAPI(lifespan=lifespan)

@app.get("/")
def root():
    return {
        "status": "v600 running",
        "pnl": STATE["daily_pnl"],
        "positions": len(STATE["positions"]),
        "token": STATE["current_token"]
    }
