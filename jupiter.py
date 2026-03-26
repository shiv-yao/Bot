import httpx

JUP_URL = "https://quote-api.jup.ag/v6"

async def get_quote(input_mint, output_mint, amount):
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{JUP_URL}/quote",
            params={
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": amount,
                "slippageBps": 100
            }
        )
        return r.json()

async def get_swap_tx(quote, user_pubkey):
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{JUP_URL}/swap",
            json={
                "quoteResponse": quote,
                "userPublicKey": str(user_pubkey),
                "wrapAndUnwrapSol": True
            }
        )
        return r.json()
