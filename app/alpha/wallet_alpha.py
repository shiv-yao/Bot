import time
from collections import defaultdict

# ================= GLOBAL =================

wallet_trades = defaultdict(list)      # wallet → pnl list
token_wallets = defaultdict(set)       # token → wallets
wallet_last_seen = {}

# ================= CONFIG =================

MIN_TRADES = 3
MIN_WINRATE = 0.4
EARLY_WINDOW = 20          # 前 N 筆交易
CLUSTER_MIN = 2            # 至少幾個 wallet 才算 cluster

# ================= RECORD =================

def record_token_wallets(mint: str, wallets: list[str]):
    if not wallets:
        return

    now = time.time()

    for w in wallets[:EARLY_WINDOW]:  # 🔥 只取 early wallets
        token_wallets[mint].add(w)
        wallet_last_seen[w] = now


def record_wallet_trade(wallet: str, pnl: float):
    wallet_trades[wallet].append(pnl)

    # 控制長度
    if len(wallet_trades[wallet]) > 50:
        wallet_trades[wallet] = wallet_trades[wallet][-50:]


# ================= WALLET SCORE =================

def get_wallet_score(wallet: str):
    trades = wallet_trades.get(wallet, [])
    if len(trades) < MIN_TRADES:
        return 0.0

    wins = sum(1 for x in trades if x > 0)
    winrate = wins / len(trades)
    avg = sum(trades) / len(trades)

    # 🔥 核心 scoring
    score = (winrate * 0.7) + (max(avg, 0) * 5)

    if winrate < MIN_WINRATE:
        return 0.0

    return min(score, 1.0)


# ================= CLUSTER =================

def get_wallet_cluster(wallets: list[str]):
    scores = [get_wallet_score(w) for w in wallets]

    strong = [s for s in scores if s > 0.2]

    if len(strong) >= CLUSTER_MIN:
        return len(strong) / len(scores)

    return 0.0


# ================= MAIN =================

def get_token_wallet_alpha(mint: str):
    wallets = list(token_wallets.get(mint, []))

    if not wallets:
        return {
            "avg": 0,
            "best": 0,
            "cluster": 0,
            "count": 0,
            "top_wallet": None,
            "copy_signal": 0
        }

    scores = [(w, get_wallet_score(w)) for w in wallets]

    # 🔥 過濾垃圾 wallet
    scores = [(w, s) for w, s in scores if s > 0]

    if not scores:
        return {
            "avg": 0,
            "best": 0,
            "cluster": 0,
            "count": 0,
            "top_wallet": None,
            "copy_signal": 0
        }

    values = [s for _, s in scores]

    avg_score = sum(values) / len(values)
    best_wallet, best_score = max(scores, key=lambda x: x[1])

    cluster = get_wallet_cluster([w for w, _ in scores])

    # 🔥 copy trading signal
    copy_signal = 1 if best_score > 0.4 else 0

    return {
        "avg": avg_score,
        "best": best_score,
        "cluster": cluster,
        "count": len(scores),
        "top_wallet": best_wallet,
        "copy_signal": copy_signal
    }


# ================= DEBUG =================

def debug_wallet_alpha(mint: str):
    data = get_token_wallet_alpha(mint)

    print(
        f"WALLET_ALPHA {mint[:6]} "
        f"avg={data['avg']:.3f} "
        f"best={data['best']:.3f} "
        f"cluster={data['cluster']:.3f} "
        f"count={data['count']} "
        f"copy={data['copy_signal']}"
    )

    return data
