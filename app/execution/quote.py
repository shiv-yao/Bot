import os
import httpx

QUOTE_URL = "https://api.jup.ag/swap/v1/quote"


def _headers():
    return {
        "x-api-key": os.getenv("JUP_API_KEY", "").strip(),
        "Accept": "application/json",
    }


async def get_quote(input_mint, output_mint, amount):
    try:
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount),
            "slippageBps": "80",
        }

        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                QUOTE_URL,
                params=params,
                headers=_headers(),
            )

        if r.status_code != 200:
            print("QUOTE ERROR STATUS:", r.status_code)
            print("QUOTE ERROR BODY:", r.text[:500])
            return None

        data = r.json()

        if not data or not data.get("outAmount"):
            print("NO ROUTE:", data)
            return None

        print("QUOTE_OK", output_mint[:8], "out=", data.get("outAmount"))
        return data

    except Exception as e:
        print("QUOTE EXCEPTION:", e)
        return None
