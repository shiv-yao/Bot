import os
import httpx

JUP_URL = "https://quote-api.jup.ag/v6/quote"
EXECUTE = os.getenv("REAL_TRADING", "false") == "true"

async def execute_swap(input_mint, output_mint, amount):
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": amount,
        "slippageBps": 100
    }

    async with httpx.AsyncClient() as c:
        r = await c.get(JUP_URL, params=params)

    if r.status_code != 200:
        return None

    quote = r.json()

    if not EXECUTE:
        print("🧪 SIMULATED TRADE")
        return quote

    # 真交易可接 /swap
    return quote
