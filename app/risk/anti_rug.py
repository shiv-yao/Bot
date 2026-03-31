from app.data.market import get_quote

SOL = "So11111111111111111111111111111111111111112"


async def anti_rug(token):
    try:
        # 先確認能買
        buy = await get_quote(SOL, token, 500_000)
        if not buy:
            print(f"RUG_BUY_FAIL {token[:8]}")
            return False

        # 再確認能賣
        # 小量測試即可，避免太嚴格
        sell = await get_quote(token, SOL, 10)
        if not sell:
            print(f"RUG_SELL_FAIL {token[:8]}")
            return False

        out_amount = float(sell.get("outAmount", 0) or 0)
        if out_amount <= 0:
            print(f"RUG_ZERO_OUT {token[:8]}")
            return False

        print(f"RUG_OK {token[:8]} sell_out={out_amount:.0f}")
        return True

    except Exception as e:
        print("ANTI_RUG ERR:", e)
        return False
