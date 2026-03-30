class RiskEngineV26:
    def __init__(self, max_dd=0.20):
        self.peak = 0
        self.cooldown_until = 0
        self.max_dd = max_dd

    def update(self, equity):
        self.peak = max(self.peak, equity)

    def drawdown(self, equity):
        if self.peak == 0:
            return 0
        return (self.peak - equity) / self.peak

    def allow_trade(self, equity):
        import time
        if time.time() < self.cooldown_until:
            return False
        return self.drawdown(equity) < self.max_dd

    def trigger_cooldown(self, sec=120):
        import time
        self.cooldown_until = time.time() + sec
