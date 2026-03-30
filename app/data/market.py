import httpx
QUOTE = "https://quote-api.jup.ag/v6/quote"
async def get_quote(input_mint, output_mint, amount):
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(QUOTE, params={"inputMint":input_mint,"outputMint":output_mint,"amount":str(amount)})
    if r.status_code != 200: return None
    d = r.json()
    if not d.get("data"): return None
    return d["data"][0]
