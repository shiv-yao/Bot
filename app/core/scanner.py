# app/core/scanner.py

import httpx

DEX_URL = "https://api.dexscreener.com/latest/dex/tokens/solana"

async def fetch_tokens():
    async with httpx.AsyncClient(timeout=5) as client:
        r = await client.get(DEX_URL)
        data = r.json()

        tokens = []

        for p in data.get("pairs", []):
            vol = p.get("volume", {}).get("h24", 0)
            change = p.get("priceChange", {}).get("h1", 0)

            if vol < 50000:
                continue

            if abs(change) < 2:
                continue

            tokens.append({
                "mint": p["baseToken"]["address"],
                "volume": vol,
                "change": change
            })

            if len(tokens) >= 15:
                break

        return tokens
