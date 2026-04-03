# ================= V27 DATA FUSION =================
import httpx
import asyncio
import time

CACHE = []
LAST_FETCH = 0

async def fetch_pump():
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get("https://frontend-api.pump.fun/coins/latest")
            if r.status_code != 200:
                return []
            data = r.json()
            return [{"mint": x["mint"], "source": "pump"} for x in data[:20]]
    except:
        return []


async def fetch_jupiter():
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get("https://token.jup.ag/all")
            data = r.json()
            return [{"mint": x["address"], "source": "jup"} for x in data[:50]]
    except:
        return []


async def fetch_dex():
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get("https://api.dexscreener.com/latest/dex/search/?q=sol")
            data = r.json()
            pairs = data.get("pairs", [])
            return [{"mint": p["baseToken"]["address"], "source": "dex"} for p in pairs[:20]]
    except:
        return []


async def fetch_candidates():
    global CACHE, LAST_FETCH

    now = time.time()

    # 🚨 throttle
    if now - LAST_FETCH < 3:
        return CACHE

    LAST_FETCH = now

    pump, jup, dex = await asyncio.gather(
        fetch_pump(),
        fetch_jupiter(),
        fetch_dex()
    )

    merged = pump + jup + dex

    # 🚨 去重
    seen = set()
    out = []
    for t in merged:
        if t["mint"] not in seen:
            seen.add(t["mint"])
            out.append(t)

    CACHE = out[:50]
    return CACHE
