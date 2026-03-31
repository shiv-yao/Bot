import os
import httpx

ORDER = "https://api.jup.ag/swap/v2/order"


def _headers():
    return {
        "x-api-key": os.getenv("JUP_API_KEY", "").strip(),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _taker():
    return os.getenv("WALLET_PUBLIC_KEY", "").strip()


async def order(input_mint, output_mint, amount, quote=None):
    try:
        payload = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount),
            "swapMode": "ExactIn",
            "slippageBps": 80,
        }

        taker = _taker()
        if taker:
            payload["taker"] = taker

        if quote:
            payload["quoteResponse"] = quote

        print("ORDER PAYLOAD:", payload)

        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(
                ORDER,
                params=payload,
                headers=_headers(),
            )

        if r.status_code != 200:
            print("ORDER ERROR STATUS:", r.status_code)
            print("ORDER ERROR BODY:", r.text)
            return None

        data = r.json()

        print("ORDER RESPONSE:", data)

        if not data:
            print("ORDER EMPTY")
            return None

        if not data.get("transaction"):
            print("ORDER NO TX:", data)
            return None

        return data

    except Exception as e:
        print("ORDER EXCEPTION:", e)
        return None
