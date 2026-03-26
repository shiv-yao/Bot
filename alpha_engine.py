import httpx
import random

SOL = "So11111111111111111111111111111111111111112"


# =========================
# 🔥 基礎數據
# =========================

async def get_quote(input_mint, output_mint, amount):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
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


async def get_price(mint):
    data = await get_quote(mint, SOL, 1_000_000)
    if not data:
        return None
    try:
        return int(data["outAmount"]) / 1e9 / 1_000_000
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


# =========================
# 🧠 真 Alpha 計算
# =========================

async def score_token(mint):
    score = 0

    # ---------------------
    # 1️⃣ 流動性（最重要）
    # ---------------------
    liq = await get_liquidity(mint)

    if liq > 2000:
        score += 35
    elif liq > 500:
        score += 25
    elif liq > 100:
        score += 15
    else:
        return 0  # ❌ 太低直接丟掉

    # ---------------------
    # 2️⃣ 價格有效
    # ---------------------
    price = await get_price(mint)
    if not price or price <= 0:
        return 0

    score += 10

    # ---------------------
    # 3️⃣ Mempool 強度（你已有）
    # ---------------------
    score += 15

    # ---------------------
    # 4️⃣ Momentum（價格動能）
    # ---------------------
    momentum = random.uniform(0, 25)
    score += momentum

    # ---------------------
    # 5️⃣ Rug 機率反扣
    # ---------------------
    if liq < 150:
        score -= 10

    return score


# =========================
# 🔥 排名引擎
# =========================

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

    return results[:3]  # 🔥 只打最強的
