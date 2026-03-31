from app.data.market import get_quote
from config.settings import SETTINGS

SOL = "So11111111111111111111111111111111111111112"


async def liquidity_ok(token):
    try:
        # 多段測試，避免只測一個 size
        for amt in [200_000, 500_000, 1_000_000]:
            q = await get_quote(SOL, token, amt)
            if not q:
                continue

            out_amount = float(q.get("outAmount", 0) or 0)
            impact = float(q.get("priceImpactPct", 1) or 1)

            if out_amount > 0 and impact < SETTINGS["LIQUIDITY_IMPACT_MAX"]:
                print(
                    f"LIQ_OK {token[:8]} amt={amt} "
                    f"out={out_amount:.0f} impact={impact:.4f}"
                )
                return True

        print(f"LIQ_FAIL {token[:8]}")
        return False

    except Exception as e:
        print("LIQUIDITY ERR:", e)
        return False
