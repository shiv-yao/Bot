# ================= v1301_FINAL =================

import asyncio, time, random
from collections import defaultdict
import httpx

from state import engine
from mempool import mempool_stream

SOL = "So11111111111111111111111111111111111111112"
PUMP_API = "https://frontend-api.pump.fun/coins/latest"

MAX_POSITION_SOL = 0.0025
MIN_POSITION_SOL = 0.001
MAX_POSITIONS = 5

HTTP = httpx.AsyncClient(timeout=10)

# ================= STATE =================

CANDIDATES = set()
TOKEN_COOLDOWN = defaultdict(float)
ALPHA_CACHE = {}
LAST_PRICE = {}

ENGINE_STATS = {
    "stable": {"pnl":0,"trades":0,"wins":0},
    "degen": {"pnl":0,"trades":0,"wins":0},
    "sniper": {"pnl":0,"trades":0,"wins":0},
}

ENGINE_ALLOCATOR = {
    "stable":0.4,
    "degen":0.4,
    "sniper":0.2,
}

ALPHA_MEMORY = {
    "stable":[],
    "degen":[],
    "sniper":[]
}

# ================= INIT =================

if not hasattr(engine, "positions"):
    engine.positions = []

engine.logs = []
engine.trade_history = []
engine.capital = 1.0
engine.sol_balance = getattr(engine, "sol_balance", 1.0)
engine.loss_streak = 0
engine.last_trade = ""
engine.last_signal = ""
engine.stats = {"signals":0,"buys":0,"sells":0,"errors":0}

# ================= LOG =================

def log(msg):
    engine.logs.append(msg)
    engine.logs = engine.logs[-200:]
    print(msg)

def now():
    return time.time()

# ================= PRICE =================

async def get_price(mint):
    try:
        r = await HTTP.get(
            "https://lite-api.jup.ag/swap/v1/quote",
            params={"inputMint":mint,"outputMint":SOL,"amount":"1000000"}
        )
        j = r.json()
        out = int(j.get("outAmount",0)) / 1e9
        return out/1_000_000 if out>0 else None
    except:
        engine.stats["errors"]+=1
        return None

# ================= TOKEN =================

async def pump_scanner():
    while True:
        try:
            r = await HTTP.get(PUMP_API)

            if r.status_code != 200:
                log(f"PUMP_HTTP_{r.status_code}")
                await asyncio.sleep(6)
                continue

            try:
                data = r.json()
            except:
                log("PUMP_BAD_JSON")
                await asyncio.sleep(6)
                continue

            for c in data[:20]:
                mint = c.get("mint")
                if mint:
                    CANDIDATES.add(mint)

            log(f"UNIVERSE {len(CANDIDATES)}")

        except Exception as e:
            log(f"PUMP_ERR {e}")

        await asyncio.sleep(6)

async def handle_mempool(e):
    m = e.get("mint")
    if m:
        CANDIDATES.add(m)

# ================= ALPHA =================

async def momentum(m):
    p1 = await get_price(m)
    await asyncio.sleep(0.08)
    p2 = await get_price(m)
    if not p1 or not p2: return 0
    return (p2-p1)/p1

async def volume(m):
    p = await get_price(m)
    if not p: return 0
    prev = LAST_PRICE.get(m,p)
    LAST_PRICE[m]=p
    return abs(p-prev)/prev if prev>0 else 0

async def alpha_engine(m):

    if m in ALPHA_CACHE and now()-ALPHA_CACHE[m][1]<3:
        return ALPHA_CACHE[m][0]

    score = (await momentum(m))*0.6 + (await volume(m))*0.4

    score = min(max(score,0),0.08)  # 防 sniper 全吃

    ALPHA_CACHE[m]=(score,now())
    return score

# ================= ENGINE =================

def update_allocator():
    w={}
    for k,v in ENGINE_STATS.items():
        if v["trades"]==0:
            w[k]=1
        else:
            win=v["wins"]/max(v["trades"],1)
            w[k]=(v["pnl"]+0.001)*win

    total=sum(abs(x) for x in w.values())+1e-9
    for k in w:
        w[k]=abs(w[k])/total

    ENGINE_ALLOCATOR.update(w)

def pick_engine(a):
    if a>0.07: return "sniper"
    if a>0.03:
        return random.choices(["stable","degen","sniper"],weights=[0.2,0.5,0.3])[0]
    return random.choices(["stable","degen","sniper"],weights=list(ENGINE_ALLOCATOR.values()))[0]

def size(a,e):
    s=MAX_POSITION_SOL*min(1,a*6)
    if engine.loss_streak>=3: s*=0.5
    return max(MIN_POSITION_SOL,min(MAX_POSITION_SOL,s*ENGINE_ALLOCATOR[e]))

# ================= EXEC =================

def can_buy(m):
    if len(engine.positions)>=MAX_POSITIONS: return False
    if any(p["token"]==m for p in engine.positions): return False
    if now()-TOKEN_COOLDOWN[m]<10: return False
    return True

async def buy(m,a):
    e=pick_engine(a)

    if not can_buy(m): return False

    price=await get_price(m)
    if not price: return False

    s=size(a,e)
    amt=s/price

    engine.positions.append({
        "token":m,"amount":amt,
        "entry_price":price,"last_price":price,
        "peak_price":price,"pnl_pct":0,
        "engine":e,"alpha":a
    })

    TOKEN_COOLDOWN[m]=now()
    engine.stats["buys"]+=1
    engine.last_trade=f"BUY {m[:6]}"
    log(f"BUY {m[:6]} {e} a={round(a,4)}")

async def sell(p):
    price=await get_price(p["token"])
    if not price: return

    pnl=(price-p["entry_price"])*p["amount"]
    e=p["engine"]

    ENGINE_STATS[e]["trades"]+=1
    ENGINE_STATS[e]["pnl"]+=pnl
    if pnl>0:
        ENGINE_STATS[e]["wins"]+=1
        engine.loss_streak=0
    else:
        engine.loss_streak+=1

    update_allocator()

    engine.capital+=pnl

    engine.trade_history.append({"side":"SELL","mint":p["token"],"result":{"pnl":pnl}})
    engine.trade_history=engine.trade_history[-200:]

    engine.positions.remove(p)

    engine.stats["sells"]+=1
    engine.last_trade=f"SELL {p['token'][:6]}"
    log(f"SELL {p['token'][:6]} pnl={round(pnl,6)}")

# ================= MONITOR =================

async def monitor():
    while True:
        for p in list(engine.positions):
            price=await get_price(p["token"])
            if not price: continue

            p["last_price"]=price
            p["peak_price"]=max(p["peak_price"],price)

            pnl=(price-p["entry_price"])/p["entry_price"]
            p["pnl_pct"]=pnl

            if pnl>0.25 or pnl<-0.08:
                await sell(p)

        await asyncio.sleep(2)

# ================= MAIN =================

async def bot():
    log("BOT_STARTED")

    asyncio.create_task(monitor())
    asyncio.create_task(pump_scanner())

    try:
        asyncio.create_task(mempool_stream(handle_mempool))
    except:
        pass

    while True:
        try:
            if len(CANDIDATES)==0:
                continue

            engine.engine_stats=ENGINE_STATS
            engine.engine_allocator=ENGINE_ALLOCATOR
            engine.candidate_count=len(CANDIDATES)

            for m in list(CANDIDATES):

                a=await alpha_engine(m)

                engine.stats["signals"]+=1
                engine.last_signal=f"{m[:6]} {round(a,4)}"

                if a<0.01: continue

                await buy(m,a)

        except Exception as e:
            engine.stats["errors"]+=1
            log(f"ERR {e}")

        await asyncio.sleep(2)

# ================= ENTRY =================

async def bot_loop():
    await bot()

if __name__=="__main__":
    asyncio.run(bot())
