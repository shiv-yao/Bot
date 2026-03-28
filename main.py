# ================= v800_FUND_ENGINE =================

import asyncio, time, aiohttp, base64, os, random
from contextlib import asynccontextmanager
from fastapi import FastAPI
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

# ================= CONFIG =================

JUP = "https://lite-api.jup.ag"
RPC = f"https://mainnet.helius-rpc.com/?api-key={os.getenv('HELIUS_API_KEY')}"

INPUT = "So11111111111111111111111111111111111111112"

MAX_POS = 5
MIN_SIZE = 0.002
MAX_SIZE = 0.02

STOP_LOSS = -0.05
TAKE_PROFIT = 0.25

MAX_DRAWDOWN = -0.1

# ================= KEY =================

keypair = Keypair.from_base58_string(os.getenv("PRIVATE_KEY"))

# ================= STATE =================

SESSION=None

STATE = {
    "positions": [],
    "closed": [],

    # 🔥 portfolio allocator
    "weights":{
        "wallet":0.25,
        "flow":0.25,
        "mempool":0.25,
        "launch":0.25
    },

    "perf":{
        "wallet":[],
        "flow":[],
        "mempool":[],
        "launch":[]
    },

    "config":{
        "alpha_threshold":100,
        "size_multiplier":1.0
    },

    "equity":0,
    "peak":0,

    "daily_pnl":0,
    "loss_streak":0,
    "kill":False
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

# ================= REAL PRICE =================

async def get_price():
    r = await get(f"{JUP}/v6/quote?inputMint={INPUT}&outputMint={INPUT}&amount=10000000")
    if not r: return None
    route=r["data"][0]
    return float(route["outAmount"])/float(route["inAmount"])

# ================= SIGNAL =================

async def compute_alpha():

    flow = random.uniform(0,50)
    mem = random.uniform(0,100)
    wallet = random.uniform(0,80)
    launch = random.choice([0,300])

    return {
        "wallet":wallet,
        "flow":flow,
        "mempool":mem,
        "launch":launch
    }

# ================= PORTFOLIO =================

def update_weights():

    weights={}
    total=0

    for k,arr in STATE["perf"].items():

        if len(arr)<5:
            score=1
        else:
            avg=sum(arr)/len(arr)
            win=sum(1 for x in arr if x>0)/len(arr)
            score=max(0.1,avg*win*10)

        weights[k]=score
        total+=score

    for k in weights:
        STATE["weights"][k]=weights[k]/total

# ================= SIZE =================

def get_size(alpha):

    size=0

    for k,v in alpha.items():
        size+=v/100 * STATE["weights"][k]

    size*=0.03 * STATE["config"]["size_multiplier"]

    if STATE["loss_streak"]>=2:
        size*=0.5

    return max(MIN_SIZE,min(size,MAX_SIZE))

# ================= EXEC =================

async def execute(sz):

    r=await get(f"{JUP}/v6/quote?inputMint={INPUT}&outputMint={INPUT}&amount={int(sz*1e9)}")
    if not r: return None,None

    route=r["data"][0]

    swap=await post(JUP+"/v6/swap",{
        "quoteResponse":route,
        "userPublicKey":str(keypair.pubkey())
    })

    tx=VersionedTransaction.from_bytes(base64.b64decode(swap["swapTransaction"]))
    tx.sign([keypair])

    raw=base64.b64encode(bytes(tx)).decode()

    await post(RPC,{
        "jsonrpc":"2.0",
        "method":"sendTransaction",
        "params":[raw]
    })

    price=float(route["outAmount"])/float(route["inAmount"])
    qty=sz/price

    return price,qty

# ================= MONITOR =================

async def monitor():

    price=await get_price()
    if not price: return

    new=[]
    equity=0

    for p in STATE["positions"]:

        pnl=(price-p["entry"])*p["qty"]
        pct=pnl/(p["entry"]*p["qty"])

        equity+=pnl

        if pct<STOP_LOSS or pct>TAKE_PROFIT:

            for s in p["sources"]:
                STATE["perf"][s].append(pnl)

            STATE["daily_pnl"]+=pnl

            if pnl>0:
                STATE["loss_streak"]=0
            else:
                STATE["loss_streak"]+=1

            STATE["closed"].append(p)
            continue

        new.append(p)

    STATE["positions"]=new
    STATE["equity"]=equity

# ================= RISK =================

def risk():

    STATE["peak"]=max(STATE["peak"],STATE["equity"])

    dd=STATE["equity"]-STATE["peak"]

    if dd<MAX_DRAWDOWN:
        STATE["kill"]=True

# ================= LOOP =================

async def bot():

    while True:

        try:

            if STATE["kill"]:
                await asyncio.sleep(5)
                continue

            await monitor()
            update_weights()
            risk()

            if STATE["daily_pnl"]<-0.1:
                await asyncio.sleep(30)
                continue

            for _ in range(3):

                if len(STATE["positions"])>=MAX_POS:
                    break

                alpha=await compute_alpha()

                total=sum(alpha.values())

                if total<STATE["config"]["alpha_threshold"]:
                    continue

                size=get_size(alpha)

                price,qty=await execute(size)

                if not price:
                    continue

                STATE["positions"].append({
                    "entry":price,
                    "qty":qty,
                    "sources":list(alpha.keys())
                })

        except:
            pass

        await asyncio.sleep(1)

# ================= APP =================

@asynccontextmanager
async def lifespan(app:FastAPI):
    global SESSION
    SESSION=aiohttp.ClientSession()
    asyncio.create_task(bot())
    yield
    await SESSION.close()

app=FastAPI(lifespan=lifespan)

@app.get("/")
def root():
    return {
        "pnl":STATE["daily_pnl"],
        "equity":STATE["equity"],
        "weights":STATE["weights"],
        "positions":len(STATE["positions"])
    }
