import time


class StrategyState:
    def __init__(self):
        self.stats = {}

    def ensure(self, source: str):
        if source not in self.stats:
            self.stats[source] = {
                "enabled": True,
                "soft_disabled": False,
                "weight": 1.0,
                "buys": 0,
                "sells": 0,
                "wins": 0,
                "losses": 0,
                "total_pnl": 0.0,
                "last_pnl": 0.0,
                "loss_streak": 0,
                "win_streak": 0,
                "route_failures": 0,
                "cooldown_until": 0.0,
                "last_buy_ts": 0.0,
                "last_sell_ts": 0.0,
            }

    def record_buy(self, source: str):
        self.ensure(source)
        s = self.stats[source]
        s["buys"] += 1
        s["last_buy_ts"] = time.time()

    def record_sell(self, source: str, pnl: float):
        self.ensure(source)
        s = self.stats[source]

        s["sells"] += 1
        s["total_pnl"] += float(pnl)
        s["last_pnl"] = float(pnl)
        s["last_sell_ts"] = time.time()

        if pnl > 0:
            s["wins"] += 1
            s["win_streak"] += 1
            s["loss_streak"] = 0

            # 有獲利時逐步清除 execution failure 壓力
            s["route_failures"] = max(0, s["route_failures"] - 1)
        else:
            s["losses"] += 1
            s["loss_streak"] += 1
            s["win_streak"] = 0

        self.reweight(source)
        self.maybe_disable(source)
        self.revive_if_recovered(source)

    def record_route_failure(self, source: str, cooldown_sec: int = 45):
        self.ensure(source)
        s = self.stats[source]
        s["route_failures"] += 1

        # execution fail 先進 cooldown，不直接永久停用
        s["cooldown_until"] = max(s["cooldown_until"], time.time() + cooldown_sec)

        # 太多次 execution fail 才進 soft disable
        if s["route_failures"] >= 3:
            s["soft_disabled"] = True

        self.reweight(source)

    def reweight(self, source: str):
        self.ensure(source)
        s = self.stats[source]

        sells = s["sells"]
        wins = s["wins"]
        total = s["total_pnl"]
        loss_streak = s["loss_streak"]
        win_streak = s["win_streak"]
        route_failures = s["route_failures"]

        weight = 1.0

        # ===== upside scaling =====
        if sells >= 2 and wins >= 2 and total > 0:
            weight = 1.15
        if sells >= 4 and wins >= 3 and total > 0.0003:
            weight = 1.30
        if sells >= 6 and wins >= 4 and total > 0.0008:
            weight = 1.50
        if sells >= 10 and wins >= 7 and total > 0.0015:
            weight = 1.80

        # ===== downside scaling =====
        if loss_streak >= 1 and total < 0:
            weight = min(weight, 0.85)
        if loss_streak >= 2 and total < 0:
            weight = min(weight, 0.60)
        if loss_streak >= 3 and total < 0:
            weight = min(weight, 0.35)
        if loss_streak >= 4 and total < 0:
            weight = min(weight, 0.15)

        # ===== recovery bonus =====
        if win_streak >= 2 and total > 0:
            weight = max(weight, 1.10)
        if win_streak >= 3 and total > 0.0005:
            weight = max(weight, 1.25)

        # ===== execution penalty =====
        if route_failures >= 1:
            weight *= 0.90
        if route_failures >= 2:
            weight *= 0.75
        if route_failures >= 3:
            weight *= 0.50

        # ===== caps =====
        if source == "fallback":
            weight = min(weight, 0.20)

        if source in ["early_buy", "fast_buy"]:
            weight = min(weight, 0.60)

        if source in ["v19_safe", "v19_seed"]:
            weight = min(weight, 0.75)

        self.stats[source]["weight"] = max(0.05, min(weight, 2.0))

    def maybe_disable(
        self,
        source: str,
        max_loss_streak: int = 4,
        min_total_pnl: float = -0.0008,
    ):
        self.ensure(source)
        s = self.stats[source]

        # route fail 太多，先 soft disable
        if s["route_failures"] >= 4:
            s["soft_disabled"] = True
            s["cooldown_until"] = max(s["cooldown_until"], time.time() + 120)

        # 連敗 + 累積虧損，進 soft disable
        if s["loss_streak"] >= max_loss_streak and s["total_pnl"] < 0:
            s["soft_disabled"] = True
            s["cooldown_until"] = max(s["cooldown_until"], time.time() + 180)

        if s["sells"] >= 4 and s["total_pnl"] <= min_total_pnl:
            s["soft_disabled"] = True
            s["cooldown_until"] = max(s["cooldown_until"], time.time() + 240)

        # 極端情況才 hard disable
        if s["weight"] <= 0.05 and s["loss_streak"] >= 5 and s["total_pnl"] < min_total_pnl * 1.5:
            s["enabled"] = False

    def revive_if_recovered(self, source: str):
        self.ensure(source)
        s = self.stats[source]

        # hard disabled 只有明顯恢復才打開
        if not s["enabled"]:
            if s["win_streak"] >= 3 or (s["total_pnl"] > 0 and s["wins"] >= s["losses"]):
                s["enabled"] = True
                s["soft_disabled"] = False
                s["cooldown_until"] = 0.0
                s["route_failures"] = 0
            return

        # soft disabled 比較容易恢復
        if s["soft_disabled"]:
            if s["win_streak"] >= 2 or s["last_pnl"] > 0:
                s["soft_disabled"] = False
                s["cooldown_until"] = 0.0
                s["route_failures"] = max(0, s["route_failures"] - 1)

    def enabled(self, source: str) -> bool:
        self.ensure(source)
        return self.stats[source]["enabled"]

    def weight(self, source: str) -> float:
        self.ensure(source)
        return self.stats[source]["weight"]

    def can_trade(self, source: str) -> bool:
        self.ensure(source)
        s = self.stats[source]

        if not s["enabled"]:
            return False

        if s["soft_disabled"] and time.time() < s["cooldown_until"]:
            return False

        if time.time() < s["cooldown_until"]:
            return False

        return True

    def disable(self, source: str):
        self.ensure(source)
        self.stats[source]["enabled"] = False

    def enable(self, source: str):
        self.ensure(source)
        self.stats[source]["enabled"] = True
        self.stats[source]["soft_disabled"] = False
        self.stats[source]["cooldown_until"] = 0.0

    def cooldown(self, source: str, seconds: int):
        self.ensure(source)
        self.stats[source]["cooldown_until"] = max(
            self.stats[source]["cooldown_until"],
            time.time() + seconds,
        )

    def summary(self):
        return self.stats
