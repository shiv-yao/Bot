from app.execution.jupiter import get_quote

REAL_TRADING = False  # 🔥 開關

async def execute_buy(mint, size):
    if not REAL_TRADING:
        return True

    quote = await get_quote("So11111111111111111111111111111111111111112", mint, int(size * 1e9))

    if not quote:
        return False

    # 👉 這裡未來接 /execute
    return True
