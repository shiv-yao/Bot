import httpx
from app.state import engine


JUP_URL = "https://quote-api.jup.ag/v6/quote"


async def safe_jupiter_order(mint):
    try:
        params = {
            "inputMint": "So11111111111111111111111111111111111111112",
            "outputMint": mint,
            "amount": 1000000,
            "slippageBps": 100,
        }

        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(JUP_URL, params=params)

        if r.status_code != 200:
            return False

        data = r.json()

        if not data.get("data"):
            return False

        return True

    except:
        return False
