# ================= v900_REAL_BATTLE =================

import asyncio, time, aiohttp, base64, os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

# ================= CONFIG =================

JUP = "https://lite-api.jup.ag"
HELIUS = os.getenv("HELIUS_API_KEY")
RPC = f"https://mainnet.helius-rpc.com/?api-key={HELIUS}"

JITO = [
    "https://mainnet.block-engine.jito.wtf/api/v1/bundles",
    "https://ny.block-engine.jito.wtf/api/v1/bundles"
]

INPUT = "So11111111111111111111111111111111111111112"

MAX_POS = 3
SIZE = 0.002

# ================= KEY =================

keypair = Keypair.from_base58_string(os.getenv("PRIVATE_KEY"))

# ================= STATE =================

SESSION=None

STATE = {
    "positions": [],
    "target_token": None,
    "sniper": False,
    "last_pump": 0,
    "last_error": None
}

# ================= SAFE =================

async def get(url):
    try:
        async with SESSION.get(url, timeout=5) as r:
            return await r.json()
    except:
        return None

async def post(url,data):
    try:
        async with SESSION.post(url,json=data,timeout=5) as r:
            return await r.json()
    except:
        return None

# ================= PUMP DETECT =================

def extract_token(logs):
    for l in logs:
        for part in l.split(" "):
            if len(part) > 30:
                return part
    return None

async def pump_ws():
    ws = RPC.replace("https","wss")

    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(ws) as w:

            await w.send_json({
                "jsonrpc":"2.0",
                "method":"logsSubscribe",
                "params":[{"mentions":["pump"]},{"commitment":"processed"}]
            })

            async for msg in w:
                data = msg.json()

                if "params" in data:
                    logs = data["params"]["result"]["value"]["logs"]

                    if any("initialize" in l for l in logs):

                        token = extract_token(logs)

                        if token:
                            STATE["target_token"] = token
                            STATE["sniper"] = True
                            STATE["last_pump"] = time.time()

# ================= FLOW =================

async def flow_alpha():
    r = await get(f"{JUP}/v6/quote?inputMint={INPUT}&outputMint={INPUT}&amount=10000000")
    if not r: return 0

    impact = float(r["data"][0].get("priceImpactPct",0))
    return max(0, 50-impact*100)

# ================= EXEC =================

async def send_jito(raw):

    await asyncio.gather(*[
        post(u,{
            "jsonrpc":"2.0",
            "method":"sendBundle",
            "params":[{"transactions":[raw],"encoding":"base64"}]
        }) for u in JITO
    ])

async def buy(token):

    r = await get(f"{JUP}/v6/quote?inputMint={INPUT}&outputMint={token}&amount={int(SIZE*1e9)}")
    if not r: return

    route = r["data"][0]

    swap = await post(JUP+"/v6/swap",{
        "quoteResponse":route,
        "userPublicKey":str(keypair.pubkey())
    })

    tx = VersionedTransaction.from_bytes(base64.b64decode(swap["swapTransaction"]))
    tx.sign([keypair])

    raw = base64.b64encode(bytes(tx)).decode()

    await send_jito(raw)

# ================= LOOP =================

async def bot():

    while True:

        try:

            # sniper reset
            if STATE["sniper"] and time.time()-STATE["last_pump"]>5:
                STATE["sniper"]=False
                STATE["target_token"]=None

            if not STATE["target_token"]:
                await asyncio.sleep(0.5)
                continue

            flow = await flow_alpha()

            # 🔥 entry condition
            if flow < 10:
                await asyncio.sleep(0.5)
                continue

            await buy(STATE["target_token"])

            STATE["positions"].append({
                "token": STATE["target_token"],
                "time": time.time()
            })

        except Exception as e:
            STATE["last_error"]=str(e)

        await asyncio.sleep(0.3)

# ================= APP =================

@asynccontextmanager
async def lifespan(app:FastAPI):
    global SESSION
    SESSION=aiohttp.ClientSession()

    asyncio.create_task(bot())
    asyncio.create_task(pump_ws())

    yield
    await SESSION.close()

app=FastAPI(lifespan=lifespan)

@app.get("/")
def root():
    return {
        "sniper": STATE["sniper"],
        "token": STATE["target_token"],
        "positions": len(STATE["positions"]),
        "error": STATE["last_error"]
    }
