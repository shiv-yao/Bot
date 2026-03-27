class StrategyState:
    def __init__(self):
        self.stats = {}

    def ensure(self, source: str):
        if source not in self.stats:
            self.stats[source] = {
                "enabled": True,
                "buys": 0,
                "sells": 0,
                "wins": 0,
                "losses": 0,
                "total_pnl": 0.0,
                "last_pnl": 0.0,
                "loss_streak": 0,
                "win_streak": 0,
            }

    def record_buy(self, source: str):
        self.ensure(source)
        self.stats[source]["buys"] += 1

    def record_sell(self, source: str, pnl: float):
        self.ensure(source)
        s = self.stats[source]

        s["sells"] += 1
        s["total_pnl"] += pnl
        s["last_pnl"] = pnl

        if pnl > 0:
            s["wins"] += 1
            s["win_streak"] += 1
            s["loss_streak"] = 0
        else:
            s["losses"] += 1
            s["loss_streak"] += 1
            s["win_streak"] = 0

    def enabled(self, source: str) -> bool:
        self.ensure(source)
        return self.stats[source]["enabled"]

    def disable(self, source: str):
        self.ensure(source)
        self.stats[source]["enabled"] = False

    def enable(self, source: str):
        self.ensure(source)
        self.stats[source]["enabled"] = True

    def maybe_disable(self, source: str, max_loss_streak: int = 3, min_total_pnl: float = -0.002):
        self.ensure(source)
        s = self.stats[source]

        if s["loss_streak"] >= max_loss_streak:
            s["enabled"] = False

        if s["total_pnl"] <= min_total_pnl:
            s["enabled"] = False

    def summary(self):
        return self.stats
