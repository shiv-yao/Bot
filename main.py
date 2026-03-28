# ================= v140_FUND_SNIPER_FULL =================

import asyncio, random, time, aiohttp, base64, os
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

JITO_ENDPOINTS = [
    "https://mainnet.block-engine.jito.wtf/api/v1/bundles",
    "https://ny.block-engine.jito.wtf/api/v1/bundles"
]

INPUT_MINT = "So11111111111111111111111111111111111111112"
OUTPUT_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

MAX_POSITIONS = 6
MIN_POSITION_SIZE = 0.01
MAX_POSITION_SIZE = 0.02

STOP_LOSS = -0.07
TAKE_PROFIT = 0.3
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

STATE = {
    "positions": [],
    "closed": [],
    "alpha_scores": [],

    "alpha_models":{
        "wallet":{"score":1,"history":[]},
        "flow":{"score":1,"history":[]},
        "mempool":{"score":1,"history":[]},
        "launch":{"score":1,"history":[]}
    },

    "alpha_rank":{
        "wallet":1,
        "flow":1,
        "mempool":1,
        "launch":1
    },

    "strategy_enabled":{
        "wallet":True,
        "flow":True,
        "mempool":True,
        "launch":True
    },

    "wallet_scores":{},
    "daily_pnl":0,
    "daily_trades":0,
    "loss_streak":0,
    "equity_peak":0,

    # 🚀 sniper
    "sniper_mode":False,
    "last_pump_time":0,

    "kill":False,
    "last_error":None
}

HEADERS={"User-Agent":"Mozilla/5.0"}

# ================= SAFE =================

async def safe_get(url):
    try:
        async with SESSION.get(url,timeout=6,headers=HEADERS) as r:
            return await r.json() if r.status==200 else None
    except:
        return None

async def safe_post(url,data):
    try:
        async with SESSION.post(url,json=data,timeout=6,headers=HEADERS) as r:
            return await r.json() if r.status==200 else None
    except:
        return None

# ================= MEMPOOL SNIPER =================

async def mempool_sniper():

    ws_url = HELIUS_RPC.replace("https","wss")

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(ws_url) as ws:

            await ws.send_json({
                "jsonrpc":"2.0",
                "id":1,
                "method":"logsSubscribe",
                "params":[{"mentions":["pump"]},{"commitment":"processed"}]
            })

            async for msg in ws:
                data = msg.json()

                if "params" in data:
                    logs = data["params"]["result"]["value"]["logs"]

                    if any("initialize" in l for l in logs):
                        STATE["sniper_mode"] = True
                        STATE["last_pump_time"] = time.time()

# ================= SIGNAL =================

def flow_signal():
    return random.uniform(0,1)*50

async def mempool_alpha():
    base=random.uniform(0,1)*100
    if STATE["sniper_mode"]:
        return base+250
    return base

async def launch_alpha():
    if random.random()<0.05:
        STATE["sniper_mode"]=True
        STATE["last_pump_time"]=time.time()
        return 300
    return 0

def wallet_alpha():
    return random.uniform(0,1)*80

# ================= SNIPER ENTRY =================

def sniper_entry():
    if not STATE["sniper_mode"]:
        return False
    if time.time()-STATE["last_pump_time"]>3:
        STATE["sniper_mode"]=False
        return False
    return True

# ================= ALPHA =================

async def compute_alpha():
    wallet=wallet_alpha()
    flow=flow_signal()
    mem=await mempool_alpha()
    launch=await launch_alpha()

    r=STATE["alpha_rank"]

    alpha = wallet*r["wallet"] + flow*r["flow"] + mem*r["mempool"] + launch*r["launch"]

    if STATE["sniper_mode"]:
        alpha+=500

    STATE["alpha_scores"].append(alpha)

    return alpha,["wallet","flow","mempool","launch"]

# ================= SIZE =================

def get_size(alpha):
    size=0.004*(1+alpha/120)

    if STATE["sniper_mode"]:
        size*=1.8

    if STATE["loss_streak"]>=2:
        size*=0.5

    return min(size,MAX_POSITION_SIZE)

# ================= EXEC =================

async def get_quote(amount):
    for api in JUP_APIS:
        url=f"{api}/v6/quote?inputMint={INPUT_MINT}&outputMint={OUTPUT_MINT}&amount={int(amount*1e9)}&slippageBps={BASE_SLIPPAGE}"
        r=await safe_get(url)
        if r and r.get("data"):
            return r
    return None

async def execute_trade(size):

    q=await get_quote(size)
    if not q: return None,None

    route=q["data"][0]

    swap=await safe_post(JUP_APIS[0]+"/v6/swap",{
        "quoteResponse":route,
        "userPublicKey":str(keypair.pubkey())
    })

    if not swap: return None,None

    tx=VersionedTransaction.from_bytes(base64.b64decode(swap["swapTransaction"]))
    tx.sign([keypair])
    raw=base64.b64encode(bytes(tx)).decode()

    await asyncio.gather(*[
        safe_post(u,{
            "jsonrpc":"2.0",
            "id":1,
            "method":"sendBundle",
            "params":[{"transactions":[raw],"encoding":"base64"}]
        }) for u in JITO_ENDPOINTS
    ])

    price=float(route["outAmount"])/float(route["inAmount"])
    qty=size/price

    return price,qty

# ================= MONITOR =================

async def monitor_positions():
    new=[]
    for p in STATE["positions"]:
        price=p["entry"]*random.uniform(0.7,1.6)
        pnl=(price-p["entry"])*p["qty"]

        if pnl < STOP_LOSS or pnl > TAKE_PROFIT:
            STATE["daily_pnl"]+=pnl

            if pnl>0:
                STATE["loss_streak"]=0
            else:
                STATE["loss_streak"]+=1

            STATE["closed"].append(p)
            continue

        new.append(p)

    STATE["positions"]=new

# ================= LOOP =================

async def bot():
    while True:
        try:
            if STATE["kill"]:
                await asyncio.sleep(2)
                continue

            await monitor_positions()

            if STATE["daily_pnl"]<DAILY_STOP:
                await asyncio.sleep(5)
                continue

            for _ in range(6):

                if len(STATE["positions"])>=MAX_POSITIONS:
                    break

                alpha,sources=await compute_alpha()

                if not (alpha>40 or sniper_entry()):
                    continue

                size=get_size(alpha)

                price,qty=await execute_trade(size)

                if not price:
                    continue

                STATE["positions"].append({
                    "entry":price,
                    "qty":qty,
                    "alpha":alpha,
                    "time":time.time()
                })

                STATE["daily_trades"]+=1

        except Exception as e:
            STATE["last_error"]=str(e)

        await asyncio.sleep(1)

# ================= API =================

bot_task=None

@asynccontextmanager
async def lifespan(app:FastAPI):
    global SESSION,bot_task
    SESSION=aiohttp.ClientSession()
    bot_task=asyncio.create_task(bot())
    asyncio.create_task(mempool_sniper())
    yield
    await SESSION.close()
    bot_task.cancel()

app=FastAPI(lifespan=lifespan)

@app.get("/dashboard",response_class=HTMLResponse)
def dashboard():
    return "<h1>🚀 V140 SNIPER RUNNING</h1>"
