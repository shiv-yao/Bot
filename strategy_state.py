class StrategyState:
    def __init__(self):
        self.stats = {}

    def ensure(self, source: str):
        if source not in self.stats:
            self.stats[source] = {
                "enabled": True,
                "weight": 1.0,
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

        self.reweight(source)
        self.maybe_disable(source)

    def reweight(self, source: str):
        self.ensure(source)
        s = self.stats[source]

        sells = s["sells"]
        wins = s["wins"]
        total = s["total_pnl"]
        loss_streak = s["loss_streak"]

        weight = 1.0

        if sells >= 2 and wins >= 2 and total > 0:
            weight = 1.25

        if sells >= 3 and wins >= 2 and total > 0.0003:
            weight = 1.5

        if sells >= 4 and wins >= 3 and total > 0.0008:
            weight = 1.8

        if loss_streak >= 1 and total < 0:
            weight = 0.75

        if loss_streak >= 2 and total < 0:
            weight = 0.5

        if loss_streak >= 3 and total < 0:
            weight = 0.0

        if source == "fallback":
            weight = min(weight, 0.30)

        if source in ["early_buy", "fast_buy"]:
            weight = min(weight, 0.70)

        s["weight"] = max(0.0, min(weight, 2.0))

    def maybe_disable(
        self,
        source: str,
        max_loss_streak: int = 3,
        min_total_pnl: float = -0.0005,
    ):
        self.ensure(source)
        s = self.stats[source]

        if s["loss_streak"] >= max_loss_streak and s["total_pnl"] < 0:
            s["enabled"] = False

        if s["sells"] >= 3 and s["total_pnl"] <= min_total_pnl:
            s["enabled"] = False

        if s["weight"] <= 0:
            s["enabled"] = False

    def enabled(self, source: str) -> bool:
        self.ensure(source)
        return self.stats[source]["enabled"]

    def weight(self, source: str) -> float:
        self.ensure(source)
        return self.stats[source]["weight"]

    def summary(self):
        return self.stats
