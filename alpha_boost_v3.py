import asyncio
import httpx

SOL = "So11111111111111111111111111111111111111112"


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


async def get_liquidity_and_impact(mint: str):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://lite-api.jup.ag/swap/v1/quote",
                params={
                    "inputMint": SOL,
                    "outputMint": mint,
                    "amount": "10000000",
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


async def liquidity_alpha(candidates: set):
    best = None
    best_score = 0

    for mint in list(candidates)[-30:]:
        liq, impact = await get_liquidity_and_impact(mint)
        if liq == 0:
            continue

        score = liq * (1 - impact)
        if impact < 0.30 and score > best_score:
            best = mint
            best_score = score

    if best and best_score > 50000:
        return best, 1500, "fusion_liquidity"

    return None, 0, None


async def momentum_alpha(candidates: set):
    for mint in list(candidates)[-20:]:
        p1 = await get_price(mint)
        await asyncio.sleep(0.10)
        p2 = await get_price(mint)
        await asyncio.sleep(0.10)
        p3 = await get_price(mint)

        if not p1 or not p2 or not p3:
            continue

        m1 = (p2 - p1) / p1
        m2 = (p3 - p2) / p2
        total = (p3 - p1) / p1

        if m1 > 0.003 and m2 > 0.003 and total > 0.008:
            return mint, 900 + total * 3000, "fusion_momentum"

    return None, 0, None


async def volume_spike_alpha(candidates: set):
    best = None
    best_score = 0

    for mint in list(candidates)[-25:]:
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
        return best, 800, "fusion_volume"

    return None, 0, None


async def anti_rug_alpha(candidates: set):
    for mint in list(candidates)[-20:]:
        liq, impact = await get_liquidity_and_impact(mint)

        if liq == 0:
            continue
        if impact > 0.35:
            continue
        if liq < 80000:
            continue

        return mint, 600, "fusion_anti_rug"

    return None, 0, None


async def alpha_fusion(candidates: set):
    if not candidates:
        return None, 0, None

    results = await asyncio.gather(
        liquidity_alpha(candidates),
        momentum_alpha(candidates),
        volume_spike_alpha(candidates),
        anti_rug_alpha(candidates),
    )

    best_mint = None
    best_score = 0
    best_source = None

    for mint, score, source in results:
        if mint and score > best_score:
            best_mint = mint
            best_score = score
            best_source = source

    return best_mint, best_score, best_source
