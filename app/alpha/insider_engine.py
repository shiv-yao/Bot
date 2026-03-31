from collections import defaultdict
import time

from app.alpha.smart_wallets import get_token_smart_score

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

    # 越少人越 insider
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
    """
    v2 insider:
    - smart wallet（績效）
    - early wallet（鏈上早期）
    """

    smart = get_token_smart_score(mint)
    early = early_wallet_score(mint)

    # 融合
    score = (smart * 0.7) + (early * 0.3)

    return round(score, 4)
