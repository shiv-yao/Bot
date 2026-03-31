from collections import defaultdict
import time

# wallet → 最近買過哪些 token
wallet_tokens = defaultdict(set)

# token → 哪些 wallet
token_wallets = defaultdict(set)

# wallet → co-occurrence 次數
wallet_graph = defaultdict(lambda: defaultdict(int))


def update_graph(mint: str, wallets: list[str]):
    now = time.time()

    for w in wallets:
        wallet_tokens[w].add(mint)
        token_wallets[mint].add(w)

    # 建立關聯（誰跟誰一起買）
    for i in range(len(wallets)):
        for j in range(i + 1, len(wallets)):
            w1 = wallets[i]
            w2 = wallets[j]

            wallet_graph[w1][w2] += 1
            wallet_graph[w2][w1] += 1


def get_connected_wallet_score(wallet: str) -> float:
    connections = wallet_graph.get(wallet, {})

    if not connections:
        return 0.0

    # 連線越多 → 越像 insider cluster
    score = sum(min(v, 5) for v in connections.values())

    return min(score / 20.0, 1.0)


def get_token_graph_score(mint: str) -> float:
    wallets = token_wallets.get(mint, [])

    if not wallets:
        return 0.0

    scores = []

    for w in wallets:
        scores.append(get_connected_wallet_score(w))

    return sum(scores) / len(scores) if scores else 0.0
