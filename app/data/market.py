import os
import re
import asyncio
import socket
import time
import httpx

from app.utils.net import resolve_host

SOL_MINT = "So11111111111111111111111111111111111111112"

JUP_ENDPOINTS = [
    "https://lite-api.jup.ag/swap/v1/quote",
    "https://quote-api.jup.ag/v6/quote",
]

_BASE58_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]+$")

QUOTE_CACHE = {}
QUOTE_CACHE_TTL = 3


def _headers():
    api_key = os.getenv("JUP_API_KEY", "").strip()
    h = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
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
        return str(v) if v > 0 else None
    except:
        return None


async def _http_get(url, params):
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            return await c.get(url, params=params, headers=_headers())
    except:
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

        async with httpx.AsyncClient(timeout=8, verify=False) as c:
            return await c.get(new_url, params=params, headers=headers)
    except:
        return None


async def get_quote(input_mint, output_mint, amount):
    if not looks_like_solana_mint(input_mint):
        return None
    if not looks_like_solana_mint(output_mint):
        return None

    amt = _normalize_amount(amount)
    if not amt:
        return None

    key = f"{input_mint}:{output_mint}:{amt}"
    now = time.time()

    if key in QUOTE_CACHE:
        if now - QUOTE_CACHE[key]["ts"] < QUOTE_CACHE_TTL:
            return QUOTE_CACHE[key]["data"]

    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": amt,
        "slippageBps": "80",
        "swapMode": "ExactIn",
    }

    for url in JUP_ENDPOINTS:
        r = await _http_get(url, params)
        if not r:
            r = await _http_get_dns(url, params)

        if not r or r.status_code != 200:
            continue

        try:
            data = r.json()
        except:
            continue

        if data.get("outAmount"):
            QUOTE_CACHE[key] = {"ts": now, "data": data}
            return data

    return None
