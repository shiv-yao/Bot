import os
import re
import httpx

QUOTE_URL = "https://lite-api.jup.ag/swap/v1/quote"

SOL_MINT = "So11111111111111111111111111111111111111112"

_BASE58_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]+$")


def _headers():
    api_key = os.getenv("JUP_API_KEY", "").strip()
    h = {"Accept": "application/json"}
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


def normalize_amount(amount) -> str | None:
    try:
        v = int(amount)
        if v <= 0:
            return None
        return str(v)
    except Exception:
        return None


async def get_quote(input_mint, output_mint, amount):
    if not looks_like_solana_mint(input_mint):
        print("MARKET INVALID INPUT_MINT:", input_mint)
        return None

    if not looks_like_solana_mint(output_mint):
        print("MARKET INVALID OUTPUT_MINT:", output_mint)
        return None

    amt = normalize_amount(amount)
    if amt is None:
        print("MARKET INVALID AMOUNT:", amount)
        return None

    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": amt,
        "slippageBps": "80",
        "swapMode": "ExactIn",
    }

    try:
        async with httpx.AsyncClient(timeout=12) as c:
            r = await c.get(
                QUOTE_URL,
                params=params,
                headers=_headers(),
            )

        if r.status_code != 200:
            print("MARKET QUOTE ERROR STATUS:", r.status_code)
            print("MARKET QUOTE ERROR PARAMS:", params)
            print("MARKET QUOTE ERROR BODY:", r.text[:500])
            return None

        data = r.json()

        if not isinstance(data, dict):
            print("MARKET QUOTE INVALID JSON:", data)
            return None

        if not data.get("outAmount"):
            print("MARKET NO ROUTE:", data)
            return None

        return data

    except Exception as e:
        print("MARKET QUOTE EXCEPTION:", repr(e))
        return None
