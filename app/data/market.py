import os
import httpx

QUOTE = "https://api.jup.ag/swap/v1/quote"

async def get_quote(input_mint, output_mint, amount):
    headers = {"x-api-key": os.getenv("JUP_API_KEY", "")}

    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(
            QUOTE,
            params={
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": str(amount),
                "slippageBps": 80,
            },
            headers=headers,
        )

    if r.status_code != 200:
        print("QUOTE ERR:", r.status_code, r.text[:300])
        return None

    data = r.json()

    # /swap/v1/quote 回來不是 v6 的 data[0] 結構
    if not data or not data.get("outAmount"):
        return None

    return data
