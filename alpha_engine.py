async def rank_candidates(candidate_pool):
    scored = []

    for mint in list(candidate_pool):
        score = await alpha_score(mint)

        if score > 5:  # 過濾垃圾
            scored.append({
                "mint": mint,
                "score": score,
            })

    scored.sort(key=lambda x: x["score"], reverse=True)

    return scored[:5]  # 只取 top 5
