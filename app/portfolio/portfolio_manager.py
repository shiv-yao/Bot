from collections import defaultdict
from statistics import mean


class PortfolioManager:
    def __init__(self):
        self.strategy_stats = defaultdict(
            lambda: {
                "pnls": [],
                "enabled": True,
                "weight": 1.0,
            }
        )

    # ===== 記錄策略績效 =====
    def record_trade(self, trade: dict):
        if not isinstance(trade, dict):
            return

        meta = trade.get("meta", {}) or {}
        strategy = meta.get("strategy") or meta.get("source") or "unknown"
        pnl = float(trade.get("pnl", 0.0) or 0.0)

        row = self.strategy_stats[strategy]
        row["pnls"].append(pnl)

        if len(row["pnls"]) > 50:
            row["pnls"].pop(0)

    # ===== 更新策略權重 =====
    def update_weights(self):
        for strategy, data in self.strategy_stats.items():
            pnls = data["pnls"]

            if len(pnls) < 5:
                data["enabled"] = True
                data["weight"] = 1.0
                continue

            avg_pnl = mean(pnls)

            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]

            if losses and sum(losses) != 0:
                pf = abs(sum(wins) / sum(losses))
            else:
                pf = 2.0 if wins else 0.0

            # ===== disable 條件 =====
            if pf < 0.8 or avg_pnl < -0.01:
                data["enabled"] = False
                data["weight"] = 0.0
                continue

            data["enabled"] = True

            # ===== 權重區間限制 =====
            weight = min(max(pf, 0.5), 2.0)
            data["weight"] = weight

    # ===== 取得某策略權重 =====
    def get_weight(self, strategy: str) -> float:
        row = self.strategy_stats.get(strategy)
        if not row:
            return 1.0

        if not row["enabled"]:
            return 0.0

        return float(row["weight"])

    # ===== 目前總曝險比例 =====
    def total_exposure_ratio(self, engine) -> float:
        capital = max(float(getattr(engine, "capital", 0.0) or 0.0), 1e-9)

        total = 0.0
        for p in getattr(engine, "positions", []) or []:
            if not isinstance(p, dict):
                continue
            total += float(p.get("size", 0.0) or 0.0)

        return total / capital

    # ===== 某策略曝險比例 =====
    def source_exposure_ratio(self, engine, strategy: str) -> float:
        capital = max(float(getattr(engine, "capital", 0.0) or 0.0), 1e-9)

        total = 0.0
        for p in getattr(engine, "positions", []) or []:
            if not isinstance(p, dict):
                continue

            meta = p.get("meta", {}) or {}
            p_strategy = meta.get("strategy") or meta.get("source") or "unknown"

            if p_strategy == strategy:
                total += float(p.get("size", 0.0) or 0.0)

        return total / capital

    # ===== debug / metrics 用 =====
    def snapshot(self):
        out = {}
        for strategy, data in self.strategy_stats.items():
            pnls = data["pnls"]
            out[strategy] = {
                "enabled": data["enabled"],
                "weight": round(float(data["weight"]), 4),
                "trades": len(pnls),
                "avg_pnl": round(mean(pnls), 4) if pnls else 0.0,
            }
        return out


portfolio = PortfolioManager()
