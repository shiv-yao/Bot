import asyncio
import httpx
import random

SOL = "So11111111111111111111111111111111111111112"


# ================================
# 工具：取得價格
# ================================
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
        out = data.get("outAmount")
        if not out:
            return None

        return int(out) / 1e9 / 1_000_000
    except Exception:
        return None


# ================================
# 工具：流動性 + 衝擊
# ================================
async def get_liquidity_and_impact(mint: str):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://lite-api.jup.ag/swap/v1/quote",
                params={
                    "inputMint": SOL,
                    "outputMint": mint,
                    "amount": "10000000",  # 0.01 SOL
                    "slippageBps": 200,
                },
            )
        if r.status_code != 200:
            return 0, 1

        data = r.json()
        out = int(data.get("outAmount", 0) or 0)
        impact = float(data.get("priceImpactPct", 1) or 1)

        return out, impact
    except Exception:
        return 0, 1


# ================================
# 1️⃣ 流動性 Alpha（真）
# impact 放寬：0.25 -> 0.35
# ================================
async def liquidity_alpha(candidates: set):
    best = None
    best_score = 0

    for mint in list(candidates)[:30]:
        liq, impact = await get_liquidity_and_impact(mint)

        if liq == 0:
            continue

        score = liq * (1 - impact)

        if impact < 0.35 and score > best_score:
            best = mint
            best_score = score

    if best and best_score > 50000:
        return best, 1500

    return None, 0


# ================================
# 2️⃣ Momentum Alpha（真）
# momentum 放寬：0.04 -> 0.02
# ================================
async def momentum_alpha(candidates: set):
    for mint in list(candidates)[:20]:
        p1 = await get_price(mint)
        await asyncio.sleep(0.05)
        p2 = await get_price(mint)

        if not p1 or not p2 or p1 <= 0:
            continue

        change = (p2 - p1) / p1

        if change > 0.02:
            return mint, 900 + change * 5000

    return None, 0


# ================================
# 3️⃣ Volume Spike Alpha
# ================================
async def volume_spike_alpha(candidates: set):
    best = None
    best_score = 0

    for mint in list(candidates)[:25]:
        liq, impact = await get_liquidity_and_impact(mint)

        if liq < 100000:
            continue

        if impact > 0.35:
            continue

        score = liq / (impact + 0.01)

        if score > best_score:
            best = mint
            best_score = score

    if best and best_score > 300000:
        return best, 800

    return None, 0


# ================================
# 4️⃣ Rug Filter Alpha
# ================================
async def anti_rug_alpha(candidates: set):
    for mint in list(candidates)[:20]:
        liq, impact = await get_liquidity_and_impact(mint)

        if liq == 0:
            continue

        if impact > 0.4:
            continue

        if liq < 50000:
            continue

        return mint, 600

    return None, 0


# ================================
# ⭐ Alpha Fusion（核心）
# 加 fallback，避免完全沒訊號
# ================================
async def alpha_fusion(candidates: set):
    tasks = [
        liquidity_alpha(candidates),
        momentum_alpha(candidates),
        volume_spike_alpha(candidates),
        anti_rug_alpha(candidates),
    ]

    results = await asyncio.gather(*tasks)

    best_mint = None
    best_score = 0

    for mint, score in results:
        if mint and score > best_score:
            best_mint = mint
            best_score = score

    if best_mint:
        return best_mint, best_score

    if candidates:
        mint = random.choice(list(candidates))
        return mint, 400

    return None, 0
