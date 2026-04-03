import asyncio
import time
import httpx

from app.utils.net import resolve_host
from app.data.market import looks_like_solana_mint

CACHE = []
LAST_FETCH = 0
FAIL_STREAK = 0
COOLDOWN_UNTIL = 0

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
}

PUMP_URL = "https://frontend-api.pump.fun/coins/latest"
DEX_URL = "https://api.dexscreener.com/latest/dex/search/?q=sol"


async def _get(url: str):
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
            return await client.get(url, headers=HEADERS)
    except Exception as e:
        print("FUSION HTTP ERR:", repr(e))
        return None


async def _get_dns(url: str):
    try:
        host = url.split("/")[2]
        ip = resolve_host(host)
        if not ip:
            return None

        new_url = url.replace(host, ip, 1)
        headers = dict(HEADERS)
        headers["Host"] = host

        async with httpx.AsyncClient(timeout=8, follow_redirects=True, verify=False) as client:
            return await client.get(new_url, headers=headers)
    except Exception as e:
        print("FUSION DNS FAIL:", repr(e))
        return None


async def _safe_get(url: str):
    r = await _get(url)
    if r is not None:
        return r
    return await _get_dns(url)


async def fetch_pump():
    r = await _safe_get(PUMP_URL)
    if r is None:
        return []

    if r.status_code != 200:
        print("PUMP HTTP ERR:", r.status_code)
        return []

    try:
        data = r.json() or []
    except Exception as e:
        print("PUMP JSON ERR:", repr(e))
        return []

    out = []
    for x in data[:30]:
        mint = x.get("mint")
        if looks_like_solana_mint(mint):
            out.append({"mint": mint, "source": "pump"})
    return out


async def fetch_dex():
    r = await _safe_get(DEX_URL)
    if r is None:
        return []

    if r.status_code != 200:
        print("DEX HTTP ERR:", r.status_code)
        return []

    try:
        data = r.json() or {}
    except Exception as e:
        print("DEX JSON ERR:", repr(e))
        return []

    pairs = data.get("pairs", []) or []
    out = []

    for p in pairs[:80]:
        if p.get("chainId") != "solana":
            continue

        mint = ((p.get("baseToken") or {}).get("address"))
        if looks_like_solana_mint(mint):
            out.append({"mint": mint, "source": "dex"})

    return out


async def fetch_candidates():
    global CACHE, LAST_FETCH, FAIL_STREAK, COOLDOWN_UNTIL

    now = time.time()

    if now < COOLDOWN_UNTIL:
        return CACHE

    if now - LAST_FETCH < 3:
        return CACHE

    LAST_FETCH = now

    pump, dex = await asyncio.gather(
        fetch_pump(),
        fetch_dex(),
    )

    merged = pump + dex

    seen = set()
    out = []

    for item in merged:
        mint = item["mint"]
        if mint in seen:
            continue
        seen.add(mint)
        out.append(item)

    if out:
        CACHE = out[:60]
        FAIL_STREAK = 0
        return CACHE

    FAIL_STREAK += 1
    if FAIL_STREAK >= 3:
        COOLDOWN_UNTIL = time.time() + 30
        print("FUSION COOLDOWN 30s")

    return CACHE
