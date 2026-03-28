# ================= v500_ALPHA_FUND_SYSTEM =================

import asyncio, time, aiohttp, base64, os, random
from contextlib import asynccontextmanager
from fastapi import FastAPI
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

# ================= CONFIG =================

JUP="https://lite-api.jup.ag"
HELIUS=os.getenv("HELIUS_API_KEY","")
RPC=f"https://mainnet.helius-rpc.com/?api-key={HELIUS}"

INPUT="So11111111111111111111111111111111111111112"

MAX_POS=6
MAX_SIZE=0.02
MIN_SIZE=0.01

STOP_LOSS=-0.07
TAKE_PROFIT=0.3

# ================= KEY =================

keypair=Keypair.from_base58_string(os.getenv("PRIVATE_KEY"))

# ================= STATE =================

SESSION=None

STATE={
    "positions":[],
    "closed":[],

    # 🔥 insider system
    "wallet_perf":{},   # wallet -> pnl list
    "top_wallets":[],

    # 🔥 alpha weights
    "alpha_weight":{
        "wallet":1,
        "flow":1,
        "mempool":1,
        "launch":1
    },

    "alpha_perf":{
        "wallet":[],
        "flow":[],
        "mempool":[],
        "launch":[]
    },

    "sniper":False,
    "last_pump":0,

    "daily_pnl":0,
    "loss_streak":0
}

# ================= SAFE =================

async def get(url):
    try:
        async with SESSION.get(url,timeout=6) as r:
            return await r.json()
    except:
        return None

async def post(url,data):
    try:
        async with SESSION.post(url,json=data,timeout=6) as r:
            return await r.json()
    except:
        return None

# ================= INSIDER ENGINE =================

async def fetch_wallets():
    # 🔥 抓最近交易（簡化版）
    r=await post(RPC,{
        "jsonrpc":"2.0",
        "method":"getRecentPerformanceSamples",
        "params":[1]
    })
    return ["walletA","walletB"]  # placeholder

def update_wallet_perf(wallet,pnl):
    arr=STATE["wallet_perf"].setdefault(wallet,[])
    arr.append(pnl)
    if len(arr)>20:
        arr.pop(0)

def rank_wallets():

    scores=[]

    for w,arr in STATE["wallet_perf"].items():
        if len(arr)<5: continue

        win=sum(1 for x in arr if x>0)/len(arr)
        avg=sum(arr)/len(arr)

        score=win*avg*100

        scores.append((w,score))

    scores.sort(key=lambda x:x[1],reverse=True)

    STATE["top_wallets"]=[w for w,_ in scores[:10]]

# ================= FLOW =================

async def flow_alpha():
    r=await get(f"{JUP}/v6/quote?inputMint={INPUT}&outputMint={INPUT}&amount=10000000")
    if not r: return 0
    impact=float(r["data"][0].get("priceImpactPct",0))
    return max(0,50-impact*100)

# ================= MEMPOOL =================

async def pump_ws():

    ws=RPC.replace("https","wss")

    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(ws) as w:

            await w.send_json({
                "jsonrpc":"2.0",
                "method":"logsSubscribe",
                "params":[{"mentions":["pump"]},{"commitment":"processed"}]
            })

            async for msg in w:
                data=msg.json()
                if "params" in data:
                    logs=data["params"]["result"]["value"]["logs"]

                    if any("initialize" in l for l in logs):
                        STATE["sniper"]=True
                        STATE["last_pump"]=time.time()

# ================= ALPHA =================

async def compute_alpha():

    flow=await flow_alpha()
    wallet=len(STATE["top_wallets"])*10
    mem=30
    launch=300 if STATE["sniper"] else 0

    w=STATE["alpha_weight"]

    total=(
        wallet*w["wallet"]
        + flow*w["flow"]
        + mem*w["mempool"]
        + launch*w["launch"]
    )

    return {
        "wallet":wallet,
        "flow":flow,
        "mempool":mem,
        "launch":launch,
        "total":total
    }

# ================= LEARNING =================

def update_alpha(alpha_sources,pnl):

    for s in alpha_sources:

        arr=STATE["alpha_perf"][s]
        arr.append(pnl)
        if len(arr)>30:
            arr.pop(0)

        win=sum(1 for x in arr if x>0)/len(arr)
        avg=sum(arr)/len(arr)

        STATE["alpha_weight"][s]=max(0.1,win*avg*10)

# ================= SIZE =================

def size(alpha):

    base=0

    for k,v in alpha.items():
        if k=="total": continue
        base+=v/100*STATE["alpha_weight"][k]

    base*=0.04

    if STATE["loss_streak"]>=2:
        base*=0.5

    return max(MIN_SIZE,min(base,MAX_SIZE))

# ================= EXEC =================

async def exec_trade(sz):

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

    price=1

    new=[]

    for p in STATE["positions"]:

        pnl=(price-p["entry"])*p["qty"]

        if pnl<STOP_LOSS or pnl>TAKE_PROFIT:

            update_alpha(p["sources"],pnl)

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

            if STATE["sniper"] and time.time()-STATE["last_pump"]>3:
                STATE["sniper"]=False

            await monitor()
            rank_wallets()

            alpha=await compute_alpha()

            if alpha["total"]<80:
                await asyncio.sleep(1)
                continue

            sz=size(alpha)

            price,qty=await exec_trade(sz)

            if price:
                STATE["positions"].append({
                    "entry":price,
                    "qty":qty,
                    "sources":[k for k in alpha if k!="total"]
                })

        except:
            pass

        await asyncio.sleep(1)

# ================= API =================

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
    return {"v500":"running"}
