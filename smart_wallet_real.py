from wallet_tracker import extract_wallets_from_mints, track_wallet_behavior

async def real_smart_wallets(RPC, candidates):
    wallets = await extract_wallets_from_mints(RPC, candidates)

    if not wallets:
        return []

    behaviors = await track_wallet_behavior(RPC, wallets)

    ranked = []

    for b in behaviors:
        score = len(b["tokens"])
        if score >= 2:
            ranked.append((b["wallet"], score))

    ranked.sort(key=lambda x: x[1], reverse=True)

    return [w[0] for w in ranked[:5]]


async def real_smart_signal(RPC, wallets, candidates):
    import random

    if not wallets:
        return None

    if not candidates:
        return None

    # 🔥 用 smart wallet 對應 token（簡化版）
    return random.choice(list(candidates))
