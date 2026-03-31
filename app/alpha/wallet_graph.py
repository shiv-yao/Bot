from collections import defaultdict
import time

# ================= STATE =================

# wallet → tokens
wallet_tokens = defaultdict(set)

# token → wallets
token_wallets = defaultdict(set)

# wallet → wallet 關聯強度
wallet_graph = defaultdict(lambda: defaultdict(int))

# wallet 最後活躍時間
wallet_last_seen = {}

# token 最後更新時間
token_last_seen = {}


# ================= UPDATE =================

def update_graph(mint: str, wallets: list[str]):
    now = time.time()

    if not wallets:
        return

    token_last_seen[mint] = now

    # 記錄 wallet 與 token
    for w in wallets:
        wallet_tokens[w].add(mint)
        token_wallets[mint].add(w)
        wallet_last_seen[w] = now

    # 建立 wallet 關聯（co-buy graph）
    n = len(wallets)
    for i in range(n):
        w1 = wallets[i]
        for j in range(i + 1, n):
            w2 = wallets[j]

            wallet_graph[w1][w2] += 1
            wallet_graph[w2][w1] += 1


# ================= SCORE =================

def get_wallet_activity_score(wallet: str) -> float:
    """
    活躍 wallet（近期有動作）加分
    """
    last = wallet_last_seen.get(wallet)
    if not last:
        return 0.0

    age = time.time() - last

    if age < 60:
        return 1.0
    elif age < 300:
        return 0.7
    elif age < 900:
        return 0.4
    else:
        return 0.1


def get_wallet_cluster_score(wallet: str) -> float:
    """
    wallet 是否在 cluster（常一起買）
    """
    connections = wallet_graph.get(wallet, {})

    if not connections:
        return 0.0

    # 強連線數
    strong_links = sum(1 for v in connections.values() if v >= 2)

    # 總連線強度
    total = sum(min(v, 5) for v in connections.values())

    score = strong_links * 0.2 + total * 0.05

    return min(score, 1.0)


def get_wallet_score(wallet: str) -> float:
    """
    單 wallet 綜合分數
    """
    activity = get_wallet_activity_score(wallet)
    cluster = get_wallet_cluster_score(wallet)

    return activity * 0.4 + cluster * 0.6


def get_token_graph_score(mint: str) -> float:
    """
    token 的資金網分數（核心🔥）
    """
    wallets = token_wallets.get(mint, [])

    if not wallets:
        return 0.0

    scores = []

    for w in wallets:
        scores.append(get_wallet_score(w))

    if not scores:
        return 0.0

    avg = sum(scores) / len(scores)

    # wallet 越多 → 放大（資金集中）
    density_boost = min(len(wallets) / 10.0, 1.0)

    final = avg * 0.7 + density_boost * 0.3

    return min(final, 1.0)


# ================= DEBUG =================

def get_top_wallets(n=10):
    rows = []

    for w in wallet_graph.keys():
        rows.append({
            "wallet": w,
            "score": round(get_wallet_score(w), 4),
            "connections": len(wallet_graph[w]),
            "tokens": len(wallet_tokens[w]),
        })

    rows.sort(key=lambda x: x["score"], reverse=True)
    return rows[:n]


def get_token_summary(mint: str):
    wallets = token_wallets.get(mint, [])

    return {
        "wallet_count": len(wallets),
        "graph_score": get_token_graph_score(mint),
        "wallets": wallets[:10],
    }
