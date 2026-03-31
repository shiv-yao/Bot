import os
import asyncio
import httpx

ORDER = "https://api.jup.ag/swap/v2/order"
EXEC = "https://api.jup.ag/swap/v2/execute"


def _headers():
    return {"x-api-key": os.getenv("JUP_API_KEY", "").strip()}


async def order(input_mint, output_mint, amount):
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                ORDER,
                params={
                    "inputMint": input_mint,
                    "outputMint": output_mint,
                    "amount": str(amount),
                },
                headers=_headers(),
            )

        if r.status_code != 200:
            print("ORDER ERROR:", r.status_code, r.text[:300])
            return None

        return r.json()

    except Exception as e:
        print("ORDER EXCEPTION:", e)
        return None


async def execute(tx):
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(EXEC, json=tx, headers=_headers())

        if r.status_code != 200:
            print("EXECUTE ERROR:", r.status_code, r.text[:300])
            return None

        return r.json()

    except Exception as e:
        print("EXECUTE EXCEPTION:", e)
        return None


async def safe_jupiter_call(tx, retries=3, delay=1):
    for i in range(retries):
        try:
            res = await execute(tx)
            if res:
                return res
            print(f"JUP RETRY {i+1}/{retries}: empty response")
            await asyncio.sleep(delay)
        except Exception as e:
            print(f"JUP ERROR {i+1}/{retries}:", e)
            await asyncio.sleep(delay)

    return None
