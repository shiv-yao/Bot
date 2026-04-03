import asyncio
import os
import time
import httpx

from app.data.market import looks_like_solana_mint

CACHE = []
LAST_FETCH = 0
CACHE_TTL = 4

HELIUS_KEY = os.getenv(“HELIUS_API_KEY”, “”)

HEADERS = {
“User-Agent”: “Mozilla/5.0”,
“Accept”: “application/json”,
“Content-Type”: “application/json”,
}

async def _get(url):
try:
async with httpx.AsyncClient(timeout=8, follow_redirects=True) as c:
return await c.get(url, headers=HEADERS)
except Exception as e:
print(“FUSION HTTP ERR:”, repr(e))
return None

async def _post(url, payload):
try:
async with httpx.AsyncClient(timeout=8, follow_redirects=True) as c:
return await c.post(url, headers=HEADERS, json=payload)
except Exception as e:
print(“FUSION POST ERR:”, repr(e))
return None

async def fetch_helius():
if not HELIUS_KEY:
return []

```
url = "https://mainnet.helius-rpc.com/?api-key=" + HELIUS_KEY
payload = {
    "jsonrpc": "2.0",
    "id": "fusion-search",
    "method": "searchAssets",
    "params": {
        "tokenType": "fungible",
        "sortBy": {"sortBy": "created", "sortDirection": "desc"},
        "limit": 40,
        "page": 1,
    },
}

r = await _post(url, payload)
if r is None or r.status_code != 200:
    print("HELIUS DAS ERR:", getattr(r, "status_code", "no_response"))
    return []

try:
    data = r.json()
except Exception as e:
    print("HELIUS DAS JSON ERR:", repr(e))
    return []

items = (data.get("result") or {}).get("items") or []
out = []
for item in items:
    mint = item.get("id")
    if looks_like_solana_mint(mint):
        out.append({"mint": mint, "source": "helius"})
return out
```

async def fetch_dex_boosts():
url = “https://api.dexscreener.com/token-boosts/latest/v1”
r = await _get(url)
if r is None:
return []
if r.status_code != 200:
print(“DEX BOOST ERR:”, r.status_code)
return []

```
try:
    data = r.json() or []
except Exception as e:
    print("DEX BOOST JSON ERR:", repr(e))
    return []

out = []
for item in data[:60]:
    if item.get("chainId") != "solana":
        continue
    mint = item.get("tokenAddress")
    if looks_like_solana_mint(mint):
        out.append({"mint": mint, "source": "dex_boost"})
return out
```

async def fetch_dex_tokens():
url = “https://api.dexscreener.com/latest/dex/tokens/solana”
r = await _get(url)
if r is None:
return []
if r.status_code != 200:
print(“DEX TOKENS ERR:”, r.status_code)
return []

```
try:
    data = r.json() or {}
except Exception as e:
    print("DEX TOKENS JSON ERR:", repr(e))
    return []

pairs = data.get("pairs") or []
out = []
for p in pairs[:80]:
    if p.get("chainId") != "solana":
        continue
    mint = (p.get("baseToken") or {}).get("address")
    if looks_like_solana_mint(mint):
        out.append({"mint": mint, "source": "dex"})
return out
```

async def fetch_candidates():
global CACHE, LAST_FETCH

```
now = time.time()
if now - LAST_FETCH < CACHE_TTL:
    return CACHE

LAST_FETCH = now

helius, boosts, tokens = await asyncio.gather(
    fetch_helius(),
    fetch_dex_boosts(),
    fetch_dex_tokens(),
    return_exceptions=True,
)

merged = []
for result in (helius, boosts, tokens):
    if isinstance(result, list):
        merged.extend(result)

seen = set()
out = []
for item in merged:
    mint = item.get("mint")
    if mint and mint not in seen:
        seen.add(mint)
        out.append(item)

if out:
    CACHE = out[:60]
    h = len(helius) if isinstance(helius, list) else "ERR"
    b = len(boosts) if isinstance(boosts, list) else "ERR"
    d = len(tokens) if isinstance(tokens, list) else "ERR"
    print("FUSION OK:", len(CACHE), "tokens helius=" + str(h), "boosts=" + str(b), "dex=" + str(d))

return CACHE
```
