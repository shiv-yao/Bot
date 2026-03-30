from app.data.market import get_quote
SOL = "So11111111111111111111111111111111111111112"
async def anti_rug(token):
    return await get_quote(token, SOL, 10) is not None
