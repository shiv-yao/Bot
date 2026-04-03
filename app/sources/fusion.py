import asyncio
import socket
import time
import httpx

from app.utils.net import resolve_host
from app.data.market import looks_like_solana_mint

CACHE = []
LAST_FETCH = 0

HEADERS = {"User-Agent": "Mozilla/5.0"}

PUMP = "https://frontend-api.pump.fun/coins/latest"
DEX = "https://api.dexscreener.com/latest/dex/search/?q=sol"
JUP = "https://token.jup.ag/all"


async def _get(url):
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            return await c.get(url, headers=HEADERS)
    except:
        return None


async def _get_dns(url):
    try:
        host = url.split("/")[2]
        ip = resolve_host(host)
        if not ip:
            return None

        new_url = url.replace(host, ip, 1)
        headers = dict(HEADERS)
        headers["Host"] = host

        async with httpx.AsyncClient(timeout=8, verify=False) as c:
            return await c.get(new_url, headers=headers)
    except:
        return None


async def fetch_candidates():
    global CACHE, LAST_FETCH

    now = time.time()
    if now - LAST_FETCH < 3:
        return CACHE

    LAST_FETCH = now

    async def safe(url):
        r = await _get(url)
        if not r:
            r = await _get_dns(url)
        return r

    pump_r, dex_r, jup_r = await asyncio.gather(
        safe(PUMP),
        safe(DEX),
        safe(JUP),
    )

    out = []

    try:
        if pump_r:
            for x in pump_r.json()[:20]:
                m = x.get("mint")
                if looks_like_solana_mint(m):
                    out.append({"mint": m, "source": "pump"})
    except:
        pass

    try:
        if dex_r:
            for p in dex_r.json().get("pairs", [])[:50]:
                if p.get("chainId") == "solana":
                    m = p.get("baseToken", {}).get("address")
                    if looks_like_solana_mint(m):
                        out.append({"mint": m, "source": "dex"})
    except:
        pass

    try:
        if jup_r:
            for x in jup_r.json()[:80]:
                m = x.get("address")
                if looks_like_solana_mint(m):
                    out.append({"mint": m, "source": "jup"})
    except:
        pass

    seen = set()
    uniq = []

    for x in out:
        if x["mint"] not in seen:
            seen.add(x["mint"])
            uniq.append(x)

    if uniq:
        CACHE = uniq[:60]

    return CACHE
