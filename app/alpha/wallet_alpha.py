from collections import defaultdict

wallet_scores = defaultdict(list)


def record_token_wallets(mint: str, wallets: list[str]):
    for w in wallets:
        wallet_scores[w].append(mint)


def get_wallet_alpha(wallets: list[str]):
    if not wallets:
        return {
            "avg": 0.0,
            "best": 0.0,
            "cluster": 0.0,
            "count": 0
        }

    scores = []

    for w in wallets:
        history = wallet_scores.get(w, [])
        score = min(len(history) * 0.1, 1.0)
        scores.append(score)

    avg = sum(scores) / len(scores)
    best = max(scores)

    return {
        "avg": round(avg, 3),
        "best": round(best, 3),
        "cluster": round(len(wallets) / 10, 3),
        "count": len(wallets)
    }
