from app.data.market import get_quote
from config.settings import SETTINGS

SOL = "So11111111111111111111111111111111111111112"


async def alpha(token):
    try:
        q = await get_quote(SOL, token, 200000)  # 0.0002 SOL
        if not q:
            print(f"ALPHA_NO_QUOTE {token[:6]}")
            return 0.0

        out_amount = float(q.get("outAmount", 0) or 0)
        impact = float(q.get("priceImpactPct", 0) or 0)

        if out_amount <= 0:
            print(f"ALPHA_ZERO_OUT {token[:6]}")
            return 0.0

        # 低 impact = 高分
        momentum = max(0.0, 0.02 - impact)

        # 先做最穩定版：只用 impact / quote 算分
        score = momentum

        print(
            f"ALPHA_OK {token[:6]} "
            f"out={out_amount:.0f} "
            f"impact={impact:.4f} "
            f"score={score:.4f}"
        )

        return score

    except Exception as e:
        print("ALPHA_ERR", token[:6], repr(e))
        return 0.0
