
from collections import defaultdict

class WalletGraph:
    def __init__(self):
        self.wallet_scores = defaultdict(float)
        self.wallet_edges = defaultdict(set)

    def update_trade(self, wallet, token, pnl):
        self.wallet_scores[wallet] += pnl
        self.wallet_edges[wallet].add(token)

    def score_wallet(self, wallet):
        base = self.wallet_scores.get(wallet, 0)
        neighbors = [w for w in self.wallet_edges if w != wallet]
        return base + sum(self.wallet_scores[w] for w in neighbors)*0.3
