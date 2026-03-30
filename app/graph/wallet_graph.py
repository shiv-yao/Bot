import httpx
DEX = "https://api.dexscreener.com/latest/dex/tokens/"
async def wallet_graph_score(token):
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(DEX + token)
    if r.status_code != 200: return 0
    data = r.json(); pairs = data.get("pairs", [])
    if not pairs: return 0
    p = pairs[0]
    buys = p.get("txns", {}).get("m5", {}).get("buys", 0)
    sells = p.get("txns", {}).get("m5", {}).get("sells", 0)
    vol = p.get("volume", {}).get("m5", 0)
    pc = p.get("priceChange", {}).get("m5", 0)
    if buys + sells == 0: return 0
    flow = buys / (buys + sells)
    score = flow
    if vol > 100000: score += 0.1
    if pc > 5: score += 0.1
    return score
