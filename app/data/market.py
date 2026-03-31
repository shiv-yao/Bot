import httpx
import random


async def get_pump():
    try:
        url = "https://frontend-api.pump.fun/coins"
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(url)
            data = r.json()

        res = []
        for c in data[:10]:
            res.append({
                "mint": c["mint"],
                "momentum": random.uniform(0, 0.05),
                "volume": c.get("volume", 0),
            })

        return res
    except:
        return []


async def get_dex():
    try:
        url = "https://api.dexscreener.com/latest/dex/tokens/solana"
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(url)
            data = r.json()

        res = []
        for t in data.get("pairs", [])[:10]:
            res.append({
                "mint": t["baseToken"]["address"],
                "momentum": float(t.get("priceChange", {}).get("h1", 0)) / 100,
                "volume": float(t.get("volume", {}).get("h24", 0)),
            })

        return res
    except:
        return []


async def get_candidates():
    pump = await get_pump()
    dex = await get_dex()

    # 🔥 合併 + 去重
    seen = set()
    result = []

    for t in pump + dex:
        if t["mint"] not in seen:
            seen.add(t["mint"])
            result.append(t)

    return result
