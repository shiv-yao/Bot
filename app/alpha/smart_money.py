import httpx

DEX_API = "https://api.dexscreener.com/latest/dex/tokens/{}"


async def fetch_pairs(mint: str):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(DEX_API.format(mint))
            if r.status_code != 200:
                return []
            data = r.json()
            return data.get("pairs", [])
    except Exception:
        return []


def calc_flow_score(pair: dict) -> float:
    try:
        buys = float(pair.get("txns", {}).get("m5", {}).get("buys", 0))
        sells = float(pair.get("txns", {}).get("m5", {}).get("sells", 0))

        if buys + sells == 0:
            return 0.0

        flow = (buys - sells) / (buys + sells)
        return max(min(flow, 1), -1)

    except Exception:
        return 0.0


def calc_volume_score(pair: dict) -> float:
    try:
        vol = float(pair.get("volume", {}).get("m5", 0))
        return min(vol / 50000, 1.0)
    except Exception:
        return 0.0


async def smart_money_score(mint: str) -> float:
    pairs = await fetch_pairs(mint)
    if not pairs:
        return 0.0

    best = 0.0

    for p in pairs[:3]:
        flow = calc_flow_score(p)
        vol = calc_volume_score(p)

        score = (flow * 0.7) + (vol * 0.3)

        if score > best:
            best = score

    return round(max(best, 0.0), 4)
