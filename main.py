# ================= v130_PRO_SYSTEM =================

import asyncio, random, time, aiohttp, base64, os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

# ================= CONFIG =================

JUP_APIS = [
    "https://lite-api.jup.ag",
    "https://quote-api.jup.ag"
]

JITO_ENDPOINTS = [
    "https://mainnet.block-engine.jito.wtf/api/v1/bundles",
    "https://ny.block-engine.jito.wtf/api/v1/bundles"
]

INPUT_MINT = "So11111111111111111111111111111111111111112"
OUTPUT_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

BASE_SLIPPAGE = 180
MAX_POSITIONS = 5
MAX_EXPOSURE = 0.1
STOP_LOSS = -0.07
TAKE_PROFIT = 0.3

# ================= KEY =================

PRIVATE_KEY = os.getenv("PRIVATE_KEY")
keypair = Keypair.from_base58_string(PRIVATE_KEY)

# ================= STATE =================

SESSION=None

STATE={
    "positions":[],
    "closed":[],
    "alpha_scores":[],

    "alpha_rank":{"wallet":1,"flow":1,"mempool":1,"launch":1},
    "alpha_models":{
        "wallet":{"history":[]},
        "flow":{"history":[]},
        "mempool":{"history":[]},
        "launch":{"history":[]}
    },

    "daily_pnl":0,
    "loss_streak":0,
    "equity_peak":0,

    "pump_signal":0,
    "kill":False
}

# ================= SAFE =================

async def safe_get(url):
    try:
        async with SESSION.get(url,timeout=8) as r:
            return await r.json()
    except:
        return None

async def safe_post(url,data):
    try:
        async with SESSION.post(url,json=data,timeout=8) as r:
            return await r.json()
    except:
        return None

# ================= SIGNAL =================

def flow_signal():
    return random.uniform(0,1)*50

async def mempool_alpha():
    base=random.uniform(0,1)*80
    if STATE["pump_signal"]:
        STATE["pump_signal"]=0
        return base+300
    return base

async def launch_alpha():
    if random.random()<0.02:
        STATE["pump_signal"]=1
        return 400
    return 0

def wallet_alpha():
    return random.uniform(0,1)*80

# ================= ALPHA =================

async def compute_alpha():

    wallet=wallet_alpha()
    flow=flow_signal()
    mem=await mempool_alpha()
    launch=await launch_alpha()

    r=STATE["alpha_rank"]

    alpha = wallet*r["wallet"] + flow*r["flow"] + mem*r["mempool"] + launch*r["launch"]

    STATE["alpha_scores"].append(alpha)

    return alpha,["wallet","flow","mempool","launch"]

# ================= ENTRY =================

STATE["alpha_hist"]=[]

def entry_signal(alpha):
    h=STATE["alpha_hist"]
    h.append(alpha)

    if len(h)<3:
        return False

    return h[-1]>h[-2]>h[-3]

# ================= FILTER =================

def liquidity_filter(route):
    try:
        inp=float(route["inAmount"])
        if inp<1e7:
            return False
        return True
    except:
        return False

# ================= SIZE =================

def get_size(alpha):
    size=0.003*(1+alpha/120)
    return min(size,0.02)

def exposure():
    return sum(p["entry"]*p["qty"] for p in STATE["positions"])

# ================= EXEC =================

async def get_quote(amount):
    for api in JUP_APIS:
        url=f"{api}/v6/quote?inputMint={INPUT_MINT}&outputMint={OUTPUT_MINT}&amount={int(amount*1e9)}&slippageBps={BASE_SLIPPAGE}"
        r=await safe_get(url)
        if r and r["data"]:
            return r
    return None

async def execute_trade(size):

    q=await get_quote(size)
    if not q:
        return None,None

    route=q["data"][0]

    if not liquidity_filter(route):
        return None,None

    swap=await safe_post(JUP_APIS[0]+"/v6/swap",{
        "quoteResponse":route,
        "userPublicKey":str(keypair.pubkey())
    })

    if not swap:
        return None,None

    tx=VersionedTransaction.from_bytes(base64.b64decode(swap["swapTransaction"]))
    tx.sign([keypair])
    raw=base64.b64encode(bytes(tx)).decode()

    await safe_post(JITO_ENDPOINTS[0],{
        "jsonrpc":"2.0",
        "id":1,
        "method":"sendBundle",
        "params":[{"transactions":[raw],"encoding":"base64"}]
    })

    price=float(route["outAmount"])/float(route["inAmount"])
    qty=size/price

    return price,qty

# ================= PRICE =================

async def get_price():
    q=await get_quote(0.01)
    if not q:
        return None
    r=q["data"][0]
    return float(r["outAmount"])/float(r["inAmount"])

# ================= PNL =================

def calc_pnl(e,p,q):
    return (p-e)*q - e*q*0.003

# ================= MONITOR =================

async def monitor():

    new=[]
    for p in STATE["positions"]:

        price=await get_price()
        if not price:
            continue

        pnl=calc_pnl(p["entry"],price,p["qty"])

        if pnl<STOP_LOSS or pnl>TAKE_PROFIT:

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

        if STATE["kill"]:
            await asyncio.sleep(2)
            continue

        await monitor()

        if exposure()>MAX_EXPOSURE:
            await asyncio.sleep(1)
            continue

        alpha,_=await compute_alpha()

        if alpha<40:
            await asyncio.sleep(1)
            continue

        if not entry_signal(alpha):
            await asyncio.sleep(1)
            continue

        size=get_size(alpha)

        price,qty=await execute_trade(size)

        if price:
            STATE["positions"].append({
                "entry":price,
                "qty":qty,
                "time":time.time()
            })

        await asyncio.sleep(1)

# ================= API =================

app=FastAPI()

@app.on_event("startup")
async def start():
    global SESSION
    SESSION=aiohttp.ClientSession()
    asyncio.create_task(bot())

@app.get("/metrics")
def metrics():
    return STATE
