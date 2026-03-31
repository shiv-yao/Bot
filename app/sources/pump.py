import httpx

URL = "https://api.dexscreener.com/latest/dex/search?q=solana"

async def fetch_pump_candidates():
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(URL)

        data = r.json()

        pairs = data.get("pairs", [])[:5]

        results = []

        for p in pairs:
            mint = p.get("baseToken", {}).get("address")
            if mint:
                results.append({"mint": mint})

        return results

    except Exception as e:
        print("PUMP FETCH ERROR:", e)
        return []
