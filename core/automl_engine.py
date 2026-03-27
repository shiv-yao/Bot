
class StrategyBrain:
    def __init__(self):
        self.scores = {"base":0}

    def update(self, pnl):
        self.scores["base"] += pnl

    def weight(self):
        return max(self.scores["base"],0)+1
