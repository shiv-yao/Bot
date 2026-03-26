import httpx

SOL = "So11111111111111111111111111111111111111112"

# 你可以先把候選池放這裡，之後再接真 scanner
CANDIDATE_MINTS = [
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
]

async def get_quote_metrics(mint: str) -> dict | None:
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
            return None
        data = r.json()
        return {
            "out_amount": int(data.get("outAmount", 0) or 0),
            "price_impact": float(data.get("priceImpactPct", 1) or 1),
        }
    except Exception:
        return None

async def alpha_score(mint: str) -> float:
    metrics = await get_quote_metrics(mint)
    if not metrics:
        return 0.0

    out_amount = metrics["out_amount"]
    impact = metrics["price_impact"]

    score = 0.0

    # 流動性代理：同樣 0.1 SOL 能換到越多、通常越容易成交
    if out_amount > 0:
        score += min(out_amount / 1_000_000, 50)

    # impact 越低越好
    if impact < 0.01:
        score += 40
    elif impact < 0.03:
        score += 25
    elif impact < 0.08:
        score += 10

    return score

async def rank_candidates():
    scored = []
    for mint in CANDIDATE_MINTS:
        score = await alpha_score(mint)
        scored.append({
            "mint": mint,
            "score": score,
        })
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored
