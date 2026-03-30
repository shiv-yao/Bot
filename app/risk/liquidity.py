from app.data.market import get_quote
from config.settings import SETTINGS
SOL = "So11111111111111111111111111111111111111112"
async def liquidity_ok(token):
    for amt in [200_000, 500_000, 1_000_000]:
        q = await get_quote(SOL, token, amt)
        if q and float(q.get("priceImpactPct", 1)) < SETTINGS["LIQUIDITY_IMPACT_MAX"]:
            return True
    return False
