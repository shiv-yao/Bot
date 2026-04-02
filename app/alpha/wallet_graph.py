from collections import defaultdict

wallet_graph = defaultdict(set)


def link_wallets(wallets):
    if not wallets:
        return

    uniq = []
    seen = set()

    for w in wallets:
        if not w or w in seen:
            continue
        seen.add(w)
        uniq.append(w)

    for i in range(len(uniq)):
        for j in range(i + 1, len(uniq)):
            a, b = uniq[i], uniq[j]
            wallet_graph[a].add(b)
            wallet_graph[b].add(a)


def cluster_score(wallet: str) -> float:
    if not wallet:
        return 0.0

    links = wallet_graph.get(wallet, set())
    return min(len(links) / 10.0, 1.0)
