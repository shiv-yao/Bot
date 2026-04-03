import asyncio
import time
import os
import httpx

from app.data.market import looks_like_solana_mint

CACHE = []
LAST_FETCH = 0

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
}

HELIUS_KEY = os.getenv("HELIUS_API_KEY", "")


async def _get(url):
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            return await c.get(url, headers=HEADERS)
    except:
        return None


async def _post(url, payload):
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            return await c.post(url, json=payload, headers=HEADERS)
    except:
        return None


async def fetch_helius():
    if not HELIUS_KEY:
        return []

    url = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}"

    payload = {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "searchAssets",
        "params": {
            "tokenType": "fungible",
            "limit": 30,
        },
    }

    r = await _post(url, payload)
    if not r or r.status_code != 200:
        return []

    data = r.json()
    items = data.get("result", {}).get("items", [])

    out = []
    for i in items:
        mint = i.get("id")
        if looks_like_solana_mint(mint):
            out.append({"mint": mint, "source": "helius"})

    return out


async def fetch_dex():
    r = await _get("https://api.dexscreener.com/latest/dex/search/?q=sol")
    if not r or r.status_code != 200:
        return []

    data = r.json()
    pairs = data.get("pairs", [])

    out = []
    for p in pairs[:50]:
        if p.get("chainId") != "solana":
            continue

        mint = (p.get("baseToken") or {}).get("address")
        if looks_like_solana_mint(mint):
            out.append({"mint": mint, "source": "dex"})

    return out


async def fetch_candidates():
    global CACHE, LAST_FETCH

    now = time.time()
    if now - LAST_FETCH < 3:
        return CACHE

    LAST_FETCH = now

    h, d = await asyncio.gather(
        fetch_helius(),
        fetch_dex(),
    )

    merged = h + d

    seen = set()
    out = []

    for x in merged:
        if x["mint"] not in seen:
            seen.add(x["mint"])
            out.append(x)

    if out:
        CACHE = out[:60]

    print("FUSION:", len(CACHE))

    return CACHE
