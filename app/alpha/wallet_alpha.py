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
