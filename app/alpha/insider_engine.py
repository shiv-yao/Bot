from collections import defaultdict
import time

# token -> [(wallet, ts)]
token_early_wallets = defaultdict(list)

# wallet -> insider hit count
wallet_insider_hits = defaultdict(int)


def record_early_wallets(mint: str, wallets: list[str]):
    """
    記錄某 token 最早一批進場 wallet
    """
    if not mint or not wallets:
        return

    now = time.time()

    existing = {w for w, _ in token_early_wallets[mint]}

    for w in wallets[:5]:
        if w not in existing:
            token_early_wallets[mint].append((w, now))

    # 只保留最早 10 個
    token_early_wallets[mint] = token_early_wallets[mint][:10]


def mark_wallet_success(wallet: str):
    """
    當某早期 wallet 對應 token 後續表現不錯時，可累積 insider hit
    """
    if wallet:
        wallet_insider_hits[wallet] += 1


def get_early_wallets(mint: str) -> list[str]:
    return [w for w, _ in token_early_wallets.get(mint, [])]


def get_wallet_insider_score(wallet: str) -> float:
    hits = wallet_insider_hits.get(wallet, 0)

    if hits <= 0:
        return 0.0

    return min(hits / 10.0, 1.0)


def get_token_insider_score(mint: str) -> float:
    wallets = get_early_wallets(mint)

    if not wallets:
        return 0.0

    scores = [get_wallet_insider_score(w) for w in wallets]

    if not scores:
        return 0.0

    # 越早期且命中越多越高
    avg = sum(scores) / len(scores)

    # 早期 wallet 數越多不一定越好，小群先買更像 insider
    crowd_penalty = 1.0
    if len(wallets) >= 6:
        crowd_penalty = 0.8
    elif len(wallets) >= 9:
        crowd_penalty = 0.6

    return min(avg * crowd_penalty, 1.0)


def get_insider_summary(mint: str) -> dict:
    wallets = token_early_wallets.get(mint, [])

    return {
        "count": len(wallets),
        "wallets": [w for w, _ in wallets[:10]],
        "score": get_token_insider_score(mint),
    }
