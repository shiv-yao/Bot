import os
import re
import asyncio
import socket
import random
import httpx
import time

SOL_MINT = "So11111111111111111111111111111111111111112"

# 🔥 多 endpoint（防炸）
JUP_ENDPOINTS = [
    "https://lite-api.jup.ag/swap/v1/quote",
    "https://quote-api.jup.ag/v6/quote",
]

_BASE58_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]+$")

# ===== QUOTE CACHE（關鍵）=====
QUOTE_CACHE = {}
CACHE_TTL = 3


def _headers():
    api_key = os.getenv("JUP_API_KEY", "").strip()
    h = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0",
    }
    if api_key:
        h["x-api-key"] = api_key
    return h


def looks_like_solana_mint(addr: str) -> bool:
    if not isinstance(addr, str):
        return False
    if addr.startswith("0x"):
        return False
    if len(addr) < 32 or len(addr) > 44:
        return False
    return bool(_BASE58_RE.fullmatch(addr))


def _normalize_amount(amount):
    try:
        v = int(amount)
        if v <= 0:
            return None
        return str(v)
    except:
        return None


# ===== DNS FIX =====
def resolve_host(host):
    try:
        return socket.gethostbyname(host)
    except:
        return None


async def _http_get(url, params):
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            return await client.get(url, params=params, headers=_headers())
    except Exception as e:
        print("MARKET HTTP ERR:", repr(e))
        return None


async def _http_get_dns(url, params):
    try:
        host = url.split("/")[2]
        ip = resolve_host(host)
        if not ip:
            return None

        new_url = url.replace(host, ip, 1)

        headers = _headers()
        headers["Host"] = host

        async with httpx.AsyncClient(timeout=6, verify=False) as client:
            print(f"DNS FIX {host}->{ip}")
            return await client.get(new_url, params=params, headers=headers)

    except Exception as e:
        print("DNS FAIL:", repr(e))
        return None


# ===== 主邏輯 =====
async def get_quote(input_mint, output_mint, amount):

    if not looks_like_solana_mint(input_mint):
        return None

    if not looks_like_solana_mint(output_mint):
        return None

    amt = _normalize_amount(amount)
    if not amt:
        return None

    # 🔥 cache key
    key = f"{input_mint}-{output_mint}-{amt}"
    now = time.time()

    if key in QUOTE_CACHE:
        data, ts = QUOTE_CACHE[key]
        if now - ts < CACHE_TTL:
            return data

    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": amt,
        "slippageBps": "80",
    }

    for url in random.sample(JUP_ENDPOINTS, len(JUP_ENDPOINTS)):

        # ===== normal =====
        r = await _http_get(url, params)

        # ===== DNS fallback =====
        if r is None:
            r = await _http_get_dns(url, params)

        if r is None:
            continue

        # ===== rate limit =====
        if r.status_code == 429:
            print("⚠️ RATE LIMITED")
            await asyncio.sleep(0.5)
            continue

        if r.status_code != 200:
            print("QUOTE ERR:", r.status_code)
            continue

        try:
            data = r.json()
        except:
            continue

        if not data.get("outAmount"):
            continue

        # 🔥 cache write
        QUOTE_CACHE[key] = (data, now)

        return data

    return None
