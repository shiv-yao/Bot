import httpx

URL = "https://api.dexscreener.com/latest/dex/search?q=solana"

def looks_like_solana_mint(addr: str) -> bool:
    return isinstance(addr, str) and not addr.startswith("0x") and 32 <= len(addr) <= 44

async def fetch_pump_candidates():
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(URL)

        if r.status_code != 200:
            print("PUMP HTTP ERR:", r.status_code)
            return []

        data = r.json()
        pairs = data.get("pairs", []) or []

        results = []
        for p in pairs:
            if p.get("chainId") != "solana":
                continue

            mint = (p.get("baseToken") or {}).get("address")
            if not looks_like_solana_mint(mint):
                continue

            results.append({"mint": mint})

        print("PUMP FILTERED:", results[:5])
        return results[:10]

    except Exception as e:
        print("PUMP FETCH ERROR:", e)
        return []
