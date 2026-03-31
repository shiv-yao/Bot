import os
import httpx

ORDER = "https://api.jup.ag/swap/v2/order"
EXEC = "https://api.jup.ag/swap/v2/execute"


def _headers():
    return {
        "x-api-key": os.getenv("JUP_API_KEY", "").strip(),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


async def order(input_mint, output_mint, amount, quote=None):
    try:
        if not quote:
            print("ORDER ERROR: missing quote")
            return None

        payload = {
            # 關鍵：原封不動帶 quote
            "quoteResponse": quote,
            "slippageBps": 80,
        }

        taker = os.getenv("WALLET_PUBLIC_KEY", "").strip()
        if taker:
            payload["taker"] = taker

        print("ORDER PAYLOAD (FINAL):", payload)

        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(
                ORDER,
                json=payload,
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


async def execute(order_data):
    try:
        payload = {
            "signedTransaction": order_data.get("signedTransaction"),
            "requestId": order_data.get("requestId"),
        }

        if not payload["signedTransaction"]:
            print("EXECUTE ERROR: missing signedTransaction")
            return None

        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(
                EXEC,
                json=payload,
                headers=_headers(),
            )

        if r.status_code != 200:
            print("EXECUTE ERROR:", r.status_code, r.text[:500])
            return None

        data = r.json()
        print("EXECUTE RESPONSE:", data)
        return data

    except Exception as e:
        print("EXECUTE EXCEPTION:", e)
        return None


async def safe_jupiter_call(order_data):
    try:
        return await execute(order_data)
    except Exception as e:
        print("JUP ERROR:", e)
        return None
