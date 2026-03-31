import os
import httpx

SWAP = "https://api.jup.ag/swap/v1/swap"


def _headers():
    api_key = os.getenv("JUP_API_KEY", "").strip()
    print("JUP KEY PRESENT:", bool(api_key))
    return {
        "x-api-key": api_key,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


async def order(input_mint, output_mint, amount, quote=None):
    try:
        if not quote:
            print("SWAP ERROR: missing quote")
            return None

        user_pk = os.getenv("WALLET_PUBLIC_KEY", "").strip()
        if not user_pk:
            print("SWAP ERROR: missing WALLET_PUBLIC_KEY")
            return None

        payload = {
            "quoteResponse": quote,
            "userPublicKey": user_pk,
            "wrapAndUnwrapSol": True,
        }

        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(SWAP, json=payload, headers=_headers())

        if r.status_code != 200:
            print("SWAP ERROR STATUS:", r.status_code)
            print("SWAP ERROR BODY:", r.text)
            return None

        data = r.json()
        print("SWAP RESPONSE:", data)

        swap_tx = data.get("swapTransaction")
        if not swap_tx:
            print("SWAP NO TX:", data)
            return None

        return {
            "transaction": swap_tx,
            "raw": data,
        }

    except Exception as e:
        print("SWAP EXCEPTION:", e)
        return None
