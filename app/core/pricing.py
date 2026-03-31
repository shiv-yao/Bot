import httpx

URL = "https://quote-api.jup.ag/v6/quote"

async def get_price(mint):
    try:
        params = {
            "inputMint": "So11111111111111111111111111111111111111112",
            "outputMint": mint,
            "amount": 1000000
        }

        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(URL, params=params)
            j = r.json()

            routes = j.get("data", [])
            if not routes:
                return None

            return int(routes[0]["outAmount"]) / 1000000
    except:
        return None
