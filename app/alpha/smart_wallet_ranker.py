from app.alpha.wallet_tracker import get_wallet_history


def wallet_score(wallet: str) -> float:
    history = get_wallet_history(wallet)

    if len(history) < 3:
        return 0.0

    score = 0.0

    for h in history[-20:]:
        if h["side"] == "buy":
            score += 1.0

    return min(score / 10.0, 1.0)


def rank_wallets(wallets: list[str]) -> float:
    if not wallets:
        return 0.0

    scores = [wallet_score(w) for w in wallets]

    if not scores:
        return 0.0

    return sum(scores) / len(scores)
