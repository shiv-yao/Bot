import os
import re
import asyncio
import httpx

SOL_MINT = "So11111111111111111111111111111111111111112"

_BASE58_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]+$")

JUP_ENDPOINTS = [
    "https://quote-api.jup.ag/v6/quote",
]

DEX_PRICE_URL = "https://api.dexscreener.com/latest/dex/tokens/"


def looks_like_solana_mint(addr):
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


def _headers():
    return {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0",
    }


async def _get_jup_quote(input_mint, output_mint, amt):
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": amt,
        "slippageBps": "80",
    }

    for url in JUP_ENDPOINTS:
        try:
            async with httpx.AsyncClient(timeout=6) as c:
                r = await c.get(url, params=params, headers=_headers())

            if r.status_code == 200:
                data = r.json()
                if data.get("outAmount"):
                    return data

            elif r.status_code == 429:
                await asyncio.sleep(1)

        except Exception:
            pass

    return None


async def _get_dex_price(mint):
    try:
        async with httpx.AsyncClient(timeout=6) as c:
            r = await c.get(DEX_PRICE_URL + mint, headers=_headers())

        if r.status_code != 200:
            return None

        data = r.json()
        pairs = data.get("pairs", [])

        for p in pairs:
            if p.get("chainId") == "solana":
                price = p.get("priceUsd")
                if price:
                    fake = float(price) * 1e6
                    return {"outAmount": str(int(fake)), "source": "dex"}

    except Exception as e:
        print("DEX ERR:", e)

    return None


async def get_quote(input_mint, output_mint, amount):
    if not looks_like_solana_mint(input_mint):
        return None
    if not looks_like_solana_mint(output_mint):
        return None

    amt = _normalize_amount(amount)
    if not amt:
        return None

    q = await _get_jup_quote(input_mint, output_mint, amt)
    if q:
        return q

    # fallback
    mint = output_mint if input_mint == SOL_MINT else input_mint
    q = await _get_dex_price(mint)
    if q:
        print("DEX FALLBACK:", mint[:6])
        return q

    return None
