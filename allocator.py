class Allocator:
    def __init__(self):
        self.weights = {}
        self.performance = {}

    def update(self, strategy: str, pnl: float):
        if strategy not in self.performance:
            self.performance[strategy] = []

        self.performance[strategy].append(float(pnl))
        self.performance[strategy] = self.performance[strategy][-30:]

    def compute_weights(self):
        scores = {}

        for strat, pnls in self.performance.items():
            if not pnls:
                continue

            avg = sum(pnls) / len(pnls)
            winrate = sum(1 for x in pnls if x > 0) / len(pnls)

            # 平均報酬 + 勝率
            score = avg * 0.7 + winrate * 0.3
            scores[strat] = max(score, 0.01)

        total = sum(scores.values()) or 1.0

        for k in scores:
            self.weights[k] = scores[k] / total

        return self.weights

    def weight(self, strategy: str) -> float:
        self.compute_weights()
        return self.weights.get(strategy, 0.05)


allocator = Allocator()
