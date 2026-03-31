import os
import asyncio
import httpx

ORDER = "https://api.jup.ag/swap/v2/order"
EXEC = "https://api.jup.ag/swap/v2/execute"


def _headers():
    return {
        "x-api-key": os.getenv("JUP_API_KEY", "").strip(),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _taker():
    return os.getenv("WALLET_PUBLIC_KEY", "").strip()


async def order(input_mint, output_mint, amount):
    try:
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount),
            "swapMode": "ExactIn",
            "slippageBps": 80,
        }

        taker = _taker()
        if taker:
            params["taker"] = taker

        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(
                ORDER,
                params=params,
                headers=_headers(),
            )

        if r.status_code != 200:
            print("ORDER ERROR:", r.status_code, r.text[:500])
            return None

        data = r.json()

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

        # 如果你現在還沒做簽名，這裡一定會失敗
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

        return r.json()

    except Exception as e:
        print("EXECUTE EXCEPTION:", e)
        return None


async def safe_jupiter_call(order_data, retries=3, delay=1):
    for i in range(retries):
        try:
            res = await execute(order_data)
            if res:
                return res
            print(f"JUP RETRY {i+1}/{retries}: empty execute response")
            await asyncio.sleep(delay)
        except Exception as e:
            print(f"JUP ERROR {i+1}/{retries}:", e)
            await asyncio.sleep(delay)

    return None
