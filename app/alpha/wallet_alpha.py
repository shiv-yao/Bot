import time
from collections import defaultdict

wallet_trades = defaultdict(list)      # wallet -> pnl list
token_wallets = defaultdict(set)       # mint -> wallets
wallet_last_seen = {}
wallet_links = defaultdict(lambda: defaultdict(int))
BLACKLIST = set()

MIN_TRADES = 3
MIN_WINRATE = 0.4
EARLY_WINDOW = 20
CLUSTER_MIN = 2


def record_token_wallets(mint: str, wallets: list[str]):
    if not wallets:
        return

    now = time.time()
    uniq = []
    seen = set()

    for w in wallets[:EARLY_WINDOW]:
        if not w or w in seen:
            continue
        seen.add(w)
        uniq.append(w)
        token_wallets[mint].add(w)
        wallet_last_seen[w] = now

    for i in range(len(uniq)):
        for j in range(i + 1, len(uniq)):
            a, b = uniq[i], uniq[j]
            wallet_links[a][b] += 1
            wallet_links[b][a] += 1


def record_wallet_result(wallet: str, pnl: float):
    if not wallet:
        wallet = "BOOTSTRAP_WALLET"

    wallet_trades[wallet].append(float(pnl))

    if len(wallet_trades[wallet]) > 50:
        wallet_trades[wallet] = wallet_trades[wallet][-50:]


def get_wallet_score(wallet: str) -> float:
    if not wallet:
        return 0.0

    if wallet in BLACKLIST:
        return 0.0

    trades = wallet_trades.get(wallet, [])
    if len(trades) < MIN_TRADES:
        return 0.01

    wins = sum(1 for x in trades if x > 0)
    winrate = wins / max(len(trades), 1)
    avg = sum(trades) / max(len(trades), 1)

    if winrate < MIN_WINRATE:
        return 0.0

    score = (winrate * 0.7) + (max(avg, 0.0) * 5.0)
    return min(score, 1.0)


def get_top_wallets(wallets: list[str], min_score=0.35):
    return [w for w in wallets if get_wallet_score(w) >= min_score]


def get_best_wallet(wallets: list[str]):
    if not wallets:
        return None
    return max(wallets, key=get_wallet_score)


def get_wallet_cluster(wallets: list[str]) -> float:
    if not wallets:
        return 0.0

    scores = [get_wallet_score(w) for w in wallets]
    strong = [s for s in scores if s > 0.2]

    if len(strong) < CLUSTER_MIN:
        return 0.0

    return len(strong) / max(len(scores), 1)


def get_token_wallet_alpha(mint: str):
    wallets = list(token_wallets.get(mint, []))

    if not wallets:
        return {
            "avg": 0.0,
            "best": 0.0,
            "cluster": 0.0,
            "count": 0,
            "top_wallet": None,
            "copy_signal": 0,
        }

    scores = [(w, get_wallet_score(w)) for w in wallets]
    scores = [(w, s) for w, s in scores if s > 0]

    if not scores:
        return {
            "avg": 0.0,
            "best": 0.0,
            "cluster": 0.0,
            "count": 0,
            "top_wallet": None,
            "copy_signal": 0,
        }

    values = [s for _, s in scores]
    avg_score = sum(values) / len(values)
    best_wallet, best_score = max(scores, key=lambda x: x[1])
    cluster = get_wallet_cluster([w for w, _ in scores])
    copy_signal = 1 if best_score > 0.4 else 0

    return {
        "avg": round(avg_score, 4),
        "best": round(best_score, 4),
        "cluster": round(cluster, 4),
        "count": len(scores),
        "top_wallet": best_wallet,
        "copy_signal": copy_signal,
    }
