import httpx, os
ORDER = "https://api.jup.ag/swap/v2/order"
EXEC = "https://api.jup.ag/swap/v2/execute"
async def order(input_mint, output_mint, amount):
    headers = {"x-api-key": os.getenv("JUP_API_KEY", "")}
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(ORDER, params={"inputMint":input_mint,"outputMint":output_mint,"amount":str(amount)}, headers=headers)
    return r.json() if r.status_code == 200 else None
async def execute(tx):
    headers = {"x-api-key": os.getenv("JUP_API_KEY", "")}
    async with httpx.AsyncClient(timeout=15) as c:
        await c.post(EXEC, json=tx, headers=headers)
