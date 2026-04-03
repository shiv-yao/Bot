import asyncio
import time
import httpx

from app.data.market import looks_like_solana_mint

CACHE = []
LAST_FETCH = 0


async def fetch_pump():
    try:
        async with httpx.AsyncClient(timeout=6) as c:
            r = await c.get("https://frontend-api.pump.fun/coins/latest")
            if r.status_code != 200:
                print("PUMP HTTP ERR:", r.status_code)
                return []
            data = r.json() or []
            out = []
            for x in data[:30]:
                mint = x.get("mint")
                if looks_like_solana_mint(mint):
                    out.append({"mint": mint, "source": "pump"})
            return out
    except Exception as e:
        print("PUMP FETCH ERR:", e)
        return []


async def fetch_jupiter():
    try:
        async with httpx.AsyncClient(timeout=6) as c:
            r = await c.get("https://token.jup.ag/all")
            if r.status_code != 200:
                print("JUP HTTP ERR:", r.status_code)
                return []
            data = r.json() or []
            out = []
            for x in data[:100]:
                mint = x.get("address")
                if looks_like_solana_mint(mint):
                    out.append({"mint": mint, "source": "jup"})
            return out
    except Exception as e:
        print("JUP FETCH ERR:", e)
        return []


async def fetch_dex():
    try:
        async with httpx.AsyncClient(timeout=6) as c:
            r = await c.get("https://api.dexscreener.com/latest/dex/search/?q=sol")
            if r.status_code != 200:
                print("DEX HTTP ERR:", r.status_code)
                return []
            data = r.json() or {}
            pairs = data.get("pairs", []) or []
            out = []
            for p in pairs[:50]:
                if p.get("chainId") != "solana":
                    continue
                mint = ((p.get("baseToken") or {}).get("address"))
                if looks_like_solana_mint(mint):
                    out.append({"mint": mint, "source": "dex"})
            return out
    except Exception as e:
        print("DEX FETCH ERR:", e)
        return []


async def fetch_candidates():
    global CACHE, LAST_FETCH

    now = time.time()
    if now - LAST_FETCH < 3:
        return CACHE

    LAST_FETCH = now

    pump, jup, dex = await asyncio.gather(
        fetch_pump(),
        fetch_jupiter(),
        fetch_dex(),
    )

    merged = pump + jup + dex

    seen = set()
    out = []
    for t in merged:
        mint = t["mint"]
        if mint in seen:
            continue
        seen.add(mint)
        out.append(t)

    CACHE = out[:50]
    return CACHE
