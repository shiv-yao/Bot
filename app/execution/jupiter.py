import os
import httpx

ORDER = "https://api.jup.ag/swap/v2/order"
EXEC = "https://api.jup.ag/swap/v2/execute"


def _headers():
    return {
        "x-api-key": os.getenv("JUP_API_KEY", "").strip(),
        "Content-Type": "application/json",
    }


async def order(input_mint, output_mint, amount):
    try:
        payload = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount),
            "slippageBps": 80,
            "userPublicKey": os.getenv("WALLET_PUBLIC_KEY"),
        }

        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(ORDER, json=payload, headers=_headers())

        if r.status_code != 200:
            print("ORDER ERROR:", r.status_code, r.text[:300])
            return None

        data = r.json()

        if not data or not data.get("transaction"):
            print("ORDER NO TX:", data)
            return None

        return data

    except Exception as e:
        print("ORDER EXCEPTION:", e)
        return None


async def execute(tx):
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(EXEC, json=tx, headers=_headers())

        if r.status_code != 200:
            print("EXECUTE ERROR:", r.status_code, r.text[:300])
            return None

        return r.json()

    except Exception as e:
        print("EXECUTE EXCEPTION:", e)
        return None


async def safe_jupiter_call(tx):
    try:
        return await execute(tx)
    except Exception as e:
        print("JUP ERROR:", e)
        return None
