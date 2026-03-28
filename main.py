# ================= v100_FUND_GOD_SYSTEM =================

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

    "pump_signal":0,
    "kill":False,

    "last_error":None
}

HEADERS={"User-Agent":"Mozilla/5.0"}

# ================= SAFE =================

async def safe_get(url):
    try:
        async with SESSION.get(url,timeout=8,headers=HEADERS) as r:
            return await r.json() if r.status==200 else None
    except:
        return None

async def safe_post(url,data):
    try:
        async with SESSION.post(url,json=data,timeout=8,headers=HEADERS) as r:
            return await r.json() if r.status==200 else None
    except:
        return None

# ================= SIGNAL =================

def flow_signal():
    return random.uniform(0,1)*50

async def mempool_alpha():
    base=random.uniform(0,1)*100
    if STATE["pump_signal"]:
        STATE["pump_signal"]=0
        return base+250
    return base

async def launch_alpha():
    if random.random()<0.05:
        STATE["pump_signal"]=1
        return 300
    return 0

def wallet_alpha():
    scores=[]
    for arr in STATE["wallet_scores"].values():
        if len(arr)<5: continue
        win=sum(1 for x in arr if x>0)/len(arr)
        avg=sum(arr)/len(arr)
        scores.append(win*avg*100)
    return sum(scores)/len(scores) if scores else 0

# ================= ALPHA =================

async def compute_alpha():
    wallet=wallet_alpha()
    flow=flow_signal()
    mem=await mempool_alpha()
    launch=await launch_alpha()

    r=STATE["alpha_rank"]

    alpha = wallet*r["wallet"] + flow*r["flow"] + mem*r["mempool"] + launch*r["launch"]

    sources=["wallet","flow","mempool","launch"]

    STATE["alpha_scores"].append(alpha)

    return alpha,sources

# ================= LEARNING =================

def update_alpha_rank(pnl,sources):
    for s in sources:
        if pnl>0:
            STATE["alpha_rank"][s]*=1.05
        else:
            STATE["alpha_rank"][s]*=0.95

def update_alpha_model(pnl,sources):
    for s in sources:
        m=STATE["alpha_models"][s]
        m["history"].append(pnl)
        if len(m["history"])>50:
            m["history"].pop(0)

# ================= FILTER =================

def strategy_filter(sources):
    return all(STATE["strategy_enabled"].get(s,True) for s in sources)

def auto_kill():
    for s,m in STATE["alpha_models"].items():
        h=m["history"]
        if len(h)<10: continue
        if sum(h)/len(h)<-0.01:
            STATE["strategy_enabled"][s]=False

# ================= SIZE =================

def get_size(alpha):
    size=0.004*(1+alpha/120)
    if STATE["loss_streak"]>=2:
        size*=0.5
    return max(MIN_POSITION_SIZE,min(size,MAX_POSITION_SIZE))

# ================= EXEC =================

async def get_quote(amount,slippage):
    for api in JUP_APIS:
        url=f"{api}/v6/quote?inputMint={INPUT_MINT}&outputMint={OUTPUT_MINT}&amount={int(amount*1e9)}&slippageBps={slippage}"
        r=await safe_get(url)
        if r and "data" in r and r["data"]:
            return r
    return None

async def get_swap(route):
    return await safe_post(f"{JUP_APIS[0]}/v6/swap",{
        "quoteResponse":route,
        "userPublicKey":str(keypair.pubkey())
    })

async def send_bundle(raw):
    res=await asyncio.gather(*[
        safe_post(u,{
            "jsonrpc":"2.0",
            "id":1,
            "method":"sendBundle",
            "params":[{"transactions":[raw],"encoding":"base64"}]
        }) for u in JITO_ENDPOINTS
    ])
    for r in res:
        if r and "result" in r:
            return r["result"]
    return None

async def execute_trade(size,alpha):
    for _ in range(3):
        q=await get_quote(size,BASE_SLIPPAGE)
        if not q: continue

        route=q["data"][0]
        swap=await get_swap(route)
        if not swap: continue

        tx=VersionedTransaction.from_bytes(base64.b64decode(swap["swapTransaction"]))
        tx.sign([keypair])
        raw=base64.b64encode(bytes(tx)).decode()

        sig=await send_bundle(raw)
        if not sig: continue

        price=float(route["outAmount"])/float(route["inAmount"])
        qty=size/price

        return price,qty

    return None,None

# ================= MONITOR =================

async def monitor_positions():
    new=[]
    for p in STATE["positions"]:

        price=p["entry"]*random.uniform(0.7,1.6)
        pnl=(price-p["entry"])*p["qty"]

        if pnl < STOP_LOSS or pnl > TAKE_PROFIT:

            update_alpha_rank(pnl,p["sources"])
            update_alpha_model(pnl,p["sources"])

            if pnl>0:
                STATE["loss_streak"]=0
            else:
                STATE["loss_streak"]+=1

            STATE["daily_pnl"]+=pnl
            STATE["closed"].append(p)
            continue

        new.append(p)

    STATE["positions"]=new

# ================= RISK =================

def risk_manager():
    pnl=STATE["daily_pnl"]
    STATE["equity_peak"]=max(STATE["equity_peak"],pnl)

    if pnl-STATE["equity_peak"]<-0.1:
        STATE["kill"]=True

# ================= LOOP =================

async def bot_loop():
    while True:
        try:
            if STATE["kill"]:
                await asyncio.sleep(2)
                continue

            auto_kill()
            risk_manager()

            await monitor_positions()

            if STATE["daily_pnl"]<DAILY_STOP:
                await asyncio.sleep(5)
                continue

            for _ in range(6):

                if len(STATE["positions"])>=MAX_POSITIONS:
                    break

                alpha,sources=await compute_alpha()

                if alpha<40:
                    continue

                if not strategy_filter(sources):
                    continue

                size=get_size(alpha)

                price,qty=await execute_trade(size,alpha)

                if not price:
                    continue

                STATE["positions"].append({
                    "entry":price,
                    "qty":qty,
                    "sources":sources,
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
    bot_task=asyncio.create_task(bot_loop())
    yield
    await SESSION.close()
    bot_task.cancel()

app=FastAPI(lifespan=lifespan)

@app.get("/")
def root():
    return {"ok":True}

@app.get("/metrics")
def metrics():
    return STATE

@app.get("/dashboard",response_class=HTMLResponse)
def dashboard():
    return "<h1>V100 RUNNING 🚀</h1>"
