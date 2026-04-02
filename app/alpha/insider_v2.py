from collections import defaultdict

early_wallets = defaultdict(list)


def record_early_wallets(mint: str, wallets: list[str]):
    if not mint or not wallets:
        return

    uniq = []
    seen = set()

    for w in wallets:
        if not w or w in seen:
            continue
        seen.add(w)
        uniq.append(w)

    if mint not in early_wallets or not early_wallets[mint]:
        early_wallets[mint] = uniq[:5]


def insider_score_v2(mint: str) -> float:
    ws = early_wallets.get(mint, [])
    if not ws:
        return 0.0

    return min(len(ws) / 5.0, 1.0)
