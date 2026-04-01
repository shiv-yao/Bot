from collections import defaultdict
import time

from app.alpha.wallet_alpha import get_token_wallet_alpha

token_early_wallets = defaultdict(list)


def record_early_wallets(mint: str, wallets: list[str]):
    if not mint or not wallets:
        return

    now = time.time()
    existing = {w for w, _ in token_early_wallets[mint]}

    for w in wallets[:5]:
        if w not in existing:
            token_early_wallets[mint].append((w, now))

    token_early_wallets[mint] = token_early_wallets[mint][:10]


def early_wallet_score(mint: str) -> float:
    wallets = token_early_wallets.get(mint, [])
    if not wallets:
        return 0.0

    count = len(wallets)
    if count <= 2:
        return 1.0
    elif count <= 5:
        return 0.7
    elif count <= 8:
        return 0.4
    else:
        return 0.2


def get_token_insider_score(mint: str) -> float:
    alpha = get_token_wallet_alpha(mint)

    avg_score = alpha["avg_score"]
    best_score = alpha["best_score"]
    cluster = alpha["cluster_score"]
    early = early_wallet_score(mint)

    score = (
        avg_score * 0.35
        + best_score * 0.25
        + cluster * 0.20
        + early * 0.20
    )

    return round(max(min(score, 1.0), 0.0), 4)
