class PortfolioManager:
    def source_exposure_ratio(self, engine, source: str) -> float:
        capital = max(float(engine.capital), 1e-9)

        exposure = sum(
            float(p.get("entry", 0.0) or 0.0) * float(p.get("size", 0.0) or 0.0)
            for p in engine.positions
            if p.get("meta", {}).get("source") == source
        )

        return exposure / capital

    def total_exposure_ratio(self, engine) -> float:
        capital = max(float(engine.capital), 1e-9)

        exposure = sum(
            float(p.get("entry", 0.0) or 0.0) * float(p.get("size", 0.0) or 0.0)
            for p in engine.positions
        )

        return exposure / capital

    def can_add_more(self, engine, max_exposure: float = 0.75) -> bool:
        return self.total_exposure_ratio(engine) < float(max_exposure)

    def allocated_exposure_for_source(self, engine, source: str) -> float:
        return sum(
            float(p.get("entry", 0.0) or 0.0) * float(p.get("size", 0.0) or 0.0)
            for p in engine.positions
            if p.get("meta", {}).get("source") == source
        )

    def weighted_position_size(
        self,
        engine,
        source: str,
        base_risk_pct: float = 0.08,
        max_position_size: float = 0.12,
        min_position_size: float = 0.02,
    ) -> float:
        """
        基礎資金分配：
        - 先用 capital * base_risk_pct 算基本風險額
        - 再按 source 做權重調整
        - 再按目前總曝險做收縮
        """

        capital = max(float(engine.capital), 0.0)
        if capital <= 0:
            return 0.0

        base = capital * float(base_risk_pct)

        # 各訊號來源上限
        source_weights = {
            "breakout": 1.00,
            "smart_money": 0.95,
            "liquidity": 0.90,
            "fusion": 1.00,
            "auto_smart": 1.10,
            "real_smart": 1.15,
            "insider": 0.85,
            "fallback": 0.50,
            "unknown": 0.70,
        }

        source_weight = source_weights.get(source, 0.75)
        size = base * source_weight

        total_exposure = self.total_exposure_ratio(engine)

        # 曝險越高，倉位越保守
        if total_exposure > 0.60:
            size *= 0.50
        elif total_exposure > 0.40:
            size *= 0.70
        elif total_exposure > 0.25:
            size *= 0.85

        # 限制單來源曝險
        source_exposure = self.source_exposure_ratio(engine, source)
        if source_exposure > 0.20:
            size *= 0.50
        elif source_exposure > 0.10:
            size *= 0.75

        size = min(size, float(max_position_size))
        size = max(size, 0.0)

        if 0 < size < float(min_position_size):
            size = float(min_position_size)

        if size > capital:
            size = capital

        return round(size, 4)


portfolio = PortfolioManager()
