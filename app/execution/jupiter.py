import os
import httpx

SWAP = "https://quote-api.jup.ag/v6/swap"


def _headers():
    return {
        "Content-Type": "application/json",
    }


async def order(input_mint, output_mint, amount, quote=None):
    try:
        if not quote:
            print("ORDER ERROR: no quote")
            return None

        payload = {
            "quoteResponse": quote,
            "userPublicKey": os.getenv("WALLET_PUBLIC_KEY"),
            "wrapAndUnwrapSol": True,
        }

        print("SWAP PAYLOAD:", payload)

        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(
                SWAP,
                json=payload,
                headers=_headers(),
            )

        if r.status_code != 200:
            print("SWAP ERROR STATUS:", r.status_code)
            print("SWAP ERROR BODY:", r.text)
            return None

        data = r.json()

        print("SWAP RESPONSE:", data)

        if not data.get("swapTransaction"):
            print("NO TX:", data)
            return None

        return {
            "transaction": data["swapTransaction"]
        }

    except Exception as e:
        print("SWAP EXCEPTION:", e)
        return None
