import httpx

QUOTE_URL = "https://quote-api.jup.ag/v6/quote"


async def get_quote(input_mint, output_mint, amount):
    try:
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount),
            "slippageBps": "80",
        }

        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(QUOTE_URL, params=params)

        if r.status_code != 200:
            print("QUOTE ERROR:", r.status_code, r.text)
            return None

        data = r.json()

        routes = data.get("data", [])
        if not routes:
            print("NO ROUTES")
            return None

        best = routes[0]

        print("QUOTE_OK", output_mint[:8], "out=", best.get("outAmount"))

        return best

    except Exception as e:
        print("QUOTE EXCEPTION:", e)
        return None
