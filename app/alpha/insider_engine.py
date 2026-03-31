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

    token_early_wallets[mint] = token_early_wallets[mint][:10]


def mark_wallet_success(wallet: str):
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
    """
    先用最簡單、最穩定的版本：
    只要 Helius 有抓到 wallet，就給 insider 分數。
    """
    from app.alpha.helius_wallet_tracker import token_wallets

    wallets = list(token_wallets.get(mint, set()))
    if not wallets:
        return 0.0

    # 1~5 個 wallet -> 0.2 ~ 1.0
    score = min(len(wallets) / 5.0, 1.0)
    return round(score, 4)


def get_insider_summary(mint: str) -> dict:
    wallets = token_early_wallets.get(mint, [])
    return {
        "count": len(wallets),
        "wallets": [w for w, _ in wallets[:10]],
        "score": get_token_insider_score(mint),
    }
