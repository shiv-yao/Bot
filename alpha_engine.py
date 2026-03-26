import httpx
import asyncio

SOL = "So11111111111111111111111111111111111111112"


async def get_liquidity(mint: str):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://lite-api.jup.ag/swap/v1/quote",
                params={
                    "inputMint": SOL,
                    "outputMint": mint,
                    "amount": "100000000",  # 0.1 SOL
                    "slippageBps": 100,
                },
            )

        if r.status_code != 200:
            return 0

        data = r.json()
        out_amount = int(data.get("outAmount", 0))

        return out_amount / 1e6  # token units
    except:
        return 0


async def get_price(mint: str):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://lite-api.jup.ag/swap/v1/quote",
                params={
                    "inputMint": mint,
                    "outputMint": SOL,
                    "amount": "1000000",
                    "slippageBps": 100,
                },
            )

        if r.status_code != 200:
            return None

        data = r.json()
        out = int(data.get("outAmount", 0))
        return out / 1e9 / 1_000_000
    except:
        return None


async def score_token(mint: str):
    score = 0.0

    # =============================
    # 1️⃣ 流動性（超重要）
    # =============================
    liquidity = await get_liquidity(mint)

    if liquidity > 1000:
        score += 30
    elif liquidity > 200:
        score += 20
    elif liquidity > 50:
        score += 10
    else:
        return 0  # ❌ 沒流動性直接淘汰

    # =============================
    # 2️⃣ 價格有效性
    # =============================
    price = await get_price(mint)
    if not price or price <= 0:
        return 0

    score += 10

    # =============================
    # 3️⃣ mempool 熱度（你外部已加）
    # =============================
    score += 10

    # =============================
    # 4️⃣ 隨機 momentum（之後換真資料）
    # =============================
    import random
    score += random.uniform(0, 20)

    return score


async def rank_candidates(candidates):
    results = []

    for mint in list(candidates):
        try:
            score = await score_token(mint)

            if score <= 0:
                continue

            results.append({
                "mint": mint,
                "score": score,
            })

        except:
            continue

    results.sort(key=lambda x: x["score"], reverse=True)

    return results[:5]  # 🔥 只留前5（關鍵）
