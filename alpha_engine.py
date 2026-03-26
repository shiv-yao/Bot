import httpx
import random

SOL = "So11111111111111111111111111111111111111112"


async def get_quote(input_mint, output_mint, amount):
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                "https://lite-api.jup.ag/swap/v1/quote",
                params={
                    "inputMint": input_mint,
                    "outputMint": output_mint,
                    "amount": str(amount),
                    "slippageBps": 100,
                },
            )
        if r.status_code != 200:
            return None
        return r.json()
    except:
        return None


async def get_liquidity(mint):
    data = await get_quote(SOL, mint, 100_000_000)
    if not data:
        return 0
    try:
        return int(data["outAmount"]) / 1e6
    except:
        return 0


async def get_price(mint):
    data = await get_quote(mint, SOL, 1_000_000)
    if not data:
        return None
    try:
        return int(data["outAmount"]) / 1e9 / 1_000_000
    except:
        return None


# 🔥 爆發判斷（核心）
def breakout_score(liq):
    score = 0

    # 新幣 + 剛有流動性 = 爆發機率高
    if 50 < liq < 300:
        score += 40

    # 中等流動性（剛開始被關注）
    elif 300 < liq < 1000:
        score += 25

    # 太大 = 已經後期
    elif liq > 2000:
        score -= 10

    return score


async def score_token(mint):
    score = 0

    liq = await get_liquidity(mint)

    # ❌ 太小 = rug
    if liq < 30:
        return 0

    # ❌ 太大 = 太晚
    if liq > 5000:
        return 0

    # 🔥 爆發核心
    score += breakout_score(liq)

    price = await get_price(mint)
    if not price:
        return 0

    score += 10

    # 🔥 mempool（你已有）
    score += 20

    # 🔥 momentum（短期波動）
    score += random.uniform(0, 30)

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

    return results[:3]
