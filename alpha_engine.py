import random


async def rank_candidates(candidates):
    results = []

    for mint in list(candidates):
        try:
            # 🔥 模擬 alpha 計算（你之後可以接真模型）
            alpha_score = 0.0

            # 1️⃣ mempool 強度（簡化）
            alpha_score += random.uniform(5, 15)

            # 2️⃣ 隨機 momentum（先頂著用）
            alpha_score += random.uniform(0, 20)

            # 3️⃣ 隨機 wallet influence（模擬 smart money）
            alpha_score += random.uniform(0, 20)

            results.append({
                "mint": mint,
                "score": alpha_score,
            })

        except Exception:
            continue

    # 🔥 排序（核心）
    results.sort(key=lambda x: x["score"], reverse=True)

    return results
