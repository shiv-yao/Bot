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
        out_amount = data.get("outAmount")
        if not out_amount:
            return None

        out_sol = int(out_amount) / 1e9
        return out_sol / 1_000_000
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
                    "amount": "10000000",  # 0.01 SOL
                    "slippageBps": 200,
                },
            )

        if r.status_code != 200:
            return 0, 1.0

        data = r.json()
        out_amount = int(data.get("outAmount", 0) or 0)
        impact = float(data.get("priceImpactPct", 1) or 1)

        return out_amount, impact
    except Exception:
        return 0, 1.0


async def liquidity_alpha(candidates: set):
    best_mint = None
    best_score = 0.0

    for mint in list(candidates)[-30:]:
        liq, impact = await get_liquidity_and_impact(mint)

        if liq <= 0:
            continue

        # 穩定版：比較保守，避免純高流動性出貨盤
        if impact > 0.30:
            continue

        if liq < 80000:
            continue

        score = liq * (1 - impact)

        if score > best_score:
            best_mint = mint
            best_score = score

    if best_mint and best_score > 50000:
        return best_mint, 1500.0, "fusion_liquidity"

    return None, 0.0, None


async def momentum_alpha(candidates: set):
    best_mint = None
    best_score = 0.0

    for mint in list(candidates)[-20:]:
        p1 = await get_price(mint)
        await asyncio.sleep(0.12)
        p2 = await get_price(mint)
        await asyncio.sleep(0.12)
        p3 = await get_price(mint)

        if not p1 or not p2 or not p3 or p1 <= 0 or p2 <= 0:
            continue

        m1 = (p2 - p1) / p1
        m2 = (p3 - p2) / p2
        total = (p3 - p1) / p1

        # 穩定版：比超嚴格版寬，但仍要求連續上升
        if m1 > 0.003 and m2 > 0.003 and total > 0.008:
            score = 900.0 + total * 3000.0
            if score > best_score:
                best_mint = mint
                best_score = score

    if best_mint:
        return best_mint, best_score, "fusion_momentum"

    return None, 0.0, None


async def volume_spike_alpha(candidates: set):
    best_mint = None
    best_score = 0.0

    for mint in list(candidates)[-25:]:
        liq, impact = await get_liquidity_and_impact(mint)

        if liq < 100000:
            continue

        if impact > 0.35:
            continue

        score = liq / (impact + 0.01)

        if score > best_score:
            best_mint = mint
            best_score = score

    if best_mint and best_score > 250000:
        return best_mint, 800.0, "fusion_volume"

    return None, 0.0, None


async def anti_rug_alpha(candidates: set):
    best_mint = None
    best_score = 0.0

    for mint in list(candidates)[-20:]:
        liq, impact = await get_liquidity_and_impact(mint)

        if liq <= 0:
            continue

        if liq < 80000:
            continue

        if impact > 0.30:
            continue

        score = liq * (1 - impact)

        if score > best_score:
            best_mint = mint
            best_score = score

    if best_mint:
        return best_mint, 600.0, "fusion_anti_rug"

    return None, 0.0, None


async def alpha_fusion(candidates: set):
    if not candidates:
        return None, 0.0, None

    results = await asyncio.gather(
        liquidity_alpha(candidates),
        momentum_alpha(candidates),
        volume_spike_alpha(candidates),
        anti_rug_alpha(candidates),
    )

    best_mint = None
    best_score = 0.0
    best_source = None

    for mint, score, source in results:
        if mint and score > best_score:
            best_mint = mint
            best_score = score
            best_source = source

    return best_mint, best_score, best_source
