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
            weight = 1.5

        if sells >= 3 and wins >= 3 and total > 0.0005:
            weight = 2.0

        if loss_streak >= 1 and total < 0:
            weight = 0.75

        if loss_streak >= 2 and total < 0:
            weight = 0.5

        if loss_streak >= 3 and total < 0:
            weight = 0.0

        # fallback 預設不要給太大倉位
        if source == "fallback":
            weight = min(weight, 0.5)

        s["weight"] = max(0.0, min(weight, 2.0))

    def maybe_disable(
        self,
        source: str,
        max_loss_streak: int = 3,
        min_total_pnl: float = -0.0005,
    ):
        self.ensure(source)
        s = self.stats[source]

        # 只有「連虧很多 + 總PnL也負」才真的關掉
        if s["loss_streak"] >= max_loss_streak and s["total_pnl"] < 0:
            s["enabled"] = False

        # 做過至少 3 筆以上還是明顯虧損，才關
        if s["sells"] >= 3 and s["total_pnl"] <= min_total_pnl:
            s["enabled"] = False

    def enabled(self, source: str) -> bool:
        self.ensure(source)
        return self.stats[source]["enabled"]

    def weight(self, source: str) -> float:
        self.ensure(source)
        return self.stats[source]["weight"]

    def disable(self, source: str):
        self.ensure(source)
        self.stats[source]["enabled"] = False

    def enable(self, source: str):
        self.ensure(source)
        self.stats[source]["enabled"] = True

    def summary(self):
        return self.stats
