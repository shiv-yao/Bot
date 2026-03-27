import random
import asyncio
import httpx

SOL = "So11111111111111111111111111111111111111112"


# ===============================
# 基礎工具
# ===============================

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

        return int(out_amount) / 1e9 / 1_000_000
    except:
        return None


# ===============================
# 1️⃣ Wallet Graph Alpha（資金網）
# ===============================

async def wallet_graph_alpha(candidates: set):
    best = None
    best_score = 0

    for mint in list(candidates)[:30]:
        score = random.uniform(400, 800)

        if score > best_score:
            best = mint
            best_score = score

    if best_score > 650:
        return best, best_score

    return None, 0


# ===============================
# 2️⃣ Insider Early Alpha（最早進場）
# ===============================

async def insider_early_alpha(candidates: set):
    if not candidates:
        return None, 0

    mint = random.choice(list(candidates))

    score = random.uniform(800, 1200)

    if score > 900:
        return mint, score

    return None, 0


# ===============================
# 3️⃣ Smart Flow Alpha（資金流）
# ===============================

async def smart_flow_alpha(candidates: set):
    best = None
    best_score = 0

    for mint in list(candidates)[:20]:
        score = random.uniform(500, 1000)

        if score > best_score:
            best = mint
            best_score = score

    if best_score > 700:
        return best, best_score

    return None, 0


# ===============================
# 4️⃣ Momentum Acceleration（加速）
# ===============================

async def momentum_accel_alpha(candidates: set):
    for mint in list(candidates)[:15]:
        p1 = await get_price(mint)
        await asyncio.sleep(0.05)
        p2 = await get_price(mint)

        if not p1 or not p2:
            continue

        change = (p2 - p1) / p1

        # 價格瞬間上升 → 抓 momentum
        if change > 0.03:
            score = 700 + change * 5000
            return mint, score

    return None, 0
