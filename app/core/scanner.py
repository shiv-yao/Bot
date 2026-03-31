import httpx

URL = "https://api.dexscreener.com/latest/dex/tokens/solana"

async def fetch_tokens():
    async with httpx.AsyncClient(timeout=5) as client:
        r = await client.get(URL)
        data = r.json()

        out = []
        for p in data.get("pairs", []):
            vol = p.get("volume", {}).get("h24", 0)
            change = p.get("priceChange", {}).get("h1", 0)

            if vol < 50000 or abs(change) < 2:
                continue

            out.append({
                "mint": p["baseToken"]["address"],
                "volume": vol,
                "change": change
            })

            if len(out) >= 10:
                break

        return out
