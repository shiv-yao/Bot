import httpx

# 🔥 用免費 endpoint（不用 API KEY）
QUOTE = "https://quote-api.jup.ag/v6/quote"


async def get_quote(input_mint, output_mint, amount):
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                QUOTE,
                params={
                    "inputMint": input_mint,
                    "outputMint": output_mint,
                    "amount": str(amount),
                    "slippageBps": 80,
                },
            )

        if r.status_code != 200:
            print("QUOTE ERR:", r.status_code, r.text[:200])
            return None

        data = r.json()

        if not data or not data.get("data"):
            return None

        return data["data"][0]

    except Exception as e:
        print("QUOTE EXCEPTION:", e)
        return None
