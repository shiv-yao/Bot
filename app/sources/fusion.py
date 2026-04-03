“””
fusion.py — 穩定版 token 來源
移除：pump.fun frontend-api（被 CF 530 擋）、token.jup.ag（DNS 失敗）
改用：

1. Helius   — getTokenLargestAccounts / searchAssets（直接 RPC，最穩）
1. DexScreener /token-boosts/latest/v1（新端點，比 search 穩定）
1. DexScreener /latest/dex/tokens/solana（備用）
   “””

import asyncio
import os
import time
import httpx

from app.data.market import looks_like_solana_mint

# ── 快取 ──────────────────────────────────────────────

CACHE: list[dict] = []
LAST_FETCH: float = 0
CACHE_TTL = 4          # 秒，比原本的 3 秒稍長一點，減少打 API 次數

# ── 設定 ─────────────────────────────────────────────

HELIUS_KEY = os.getenv(“HELIUS_API_KEY”, “”)
RPC_URL    = os.getenv(
“RPC_URL”,
f”https://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}” if HELIUS_KEY else “”
)

HEADERS = {
“User-Agent”: “Mozilla/5.0”,
“Accept”:     “application/json”,
“Content-Type”: “application/json”,
}

# ── 通用 HTTP ─────────────────────────────────────────

async def _get(url: str, **kwargs) -> httpx.Response | None:
try:
async with httpx.AsyncClient(timeout=8, follow_redirects=True) as c:
return await c.get(url, headers=HEADERS, **kwargs)
except Exception as e:
print(“FUSION HTTP ERR:”, repr(e))
return None

async def _post(url: str, payload: dict) -> httpx.Response | None:
try:
async with httpx.AsyncClient(timeout=8, follow_redirects=True) as c:
return await c.post(url, headers=HEADERS, json=payload)
except Exception as e:
print(“FUSION POST ERR:”, repr(e))
return None

# ══════════════════════════════════════════════════════

# 來源 1：Helius DAS searchAssets（鏈上真實新 token）

# ══════════════════════════════════════════════════════

async def fetch_helius() -> list[dict]:
“””
用 Helius DAS API 抓最新發行的 Solana fungible token。
完全走 Helius RPC，不受 pump.fun / jup.ag DNS 影響。
“””
if not HELIUS_KEY:
return []

```
url = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}"
payload = {
    "jsonrpc": "2.0",
    "id":      "fusion-search",
    "method":  "searchAssets",
    "params": {
        "tokenType":   "fungible",
        "sortBy":      {"sortBy": "created", "sortDirection": "desc"},
        "limit":       40,
        "page":        1,
    },
}

r = await _post(url, payload)
if r is None or r.status_code != 200:
    print("HELIUS DAS ERR:", getattr(r, "status_code", "no response"))
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

# ══════════════════════════════════════════════════════

# 來源 2：DexScreener /token-boosts（穩定新端點）

# ══════════════════════════════════════════════════════

async def fetch_dex_boosts() -> list[dict]:
“””
DexScreener 的 token-boosts API — 回傳近期被 boost 的 Solana token。
這個端點比 /latest/dex/search 更穩、也不會被 Cloudflare 攔。
“””
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
    # 只要 Solana chain
    if item.get("chainId") != "solana":
        continue
    mint = item.get("tokenAddress")
    if looks_like_solana_mint(mint):
        out.append({"mint": mint, "source": "dex_boost"})
return out
```

# ══════════════════════════════════════════════════════

# 來源 3：DexScreener /tokens/solana（備用 fallback）

# ══════════════════════════════════════════════════════

async def fetch_dex_tokens() -> list[dict]:
“””
DexScreener 按鏈抓 token list，作為備用。
原本的 /latest/dex/search?q=sol 有時 DNS 不穩，改用這個端點。
“””
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

# ══════════════════════════════════════════════════════

# 主入口：fetch_candidates

# ══════════════════════════════════════════════════════

async def fetch_candidates() -> list[dict]:
global CACHE, LAST_FETCH

```
now = time.time()
if now - LAST_FETCH < CACHE_TTL:
    return CACHE

LAST_FETCH = now

# 三個來源並發抓，任何一個失敗不影響其他
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
    # 如果是 Exception 就跳過

# 去重
seen: set[str] = set()
out: list[dict] = []
for item in merged:
    mint = item.get("mint")
    if mint and mint not in seen:
        seen.add(mint)
        out.append(item)

if out:
    CACHE = out[:60]
    print(f"FUSION OK: {len(CACHE)} tokens "
          f"(helius={len(helius) if isinstance(helius, list) else 'ERR'}, "
          f"boosts={len(boosts) if isinstance(boosts, list) else 'ERR'}, "
          f"dex={len(tokens) if isinstance(tokens, list) else 'ERR'})")

return CACHE
```

# ── 單獨測試用 ────────────────────────────────────────

if **name** == “**main**”:
import json

```
async def _test():
    results = await fetch_candidates()
    print(json.dumps(results[:10], indent=2))

asyncio.run(_test())
```
