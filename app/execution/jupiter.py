import httpx

JUP_URL = "https://quote-api.jup.ag/v6/quote"

async def get_quote(input_mint, output_mint, amount):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(JUP_URL, params={
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": amount,
                "slippageBps": 50
            })

        if r.status_code != 200:
            return None

        return r.json()

    except Exception:
        return None
