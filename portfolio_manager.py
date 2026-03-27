class PortfolioManagerV26:
    def __init__(self, engine):
        self.engine = engine

    def total_exposure(self):
        total = 0
        for p in self.engine.positions:
            total += p["entry_price"] * p["amount"]
        return total

    def exposure_ratio(self):
        capital = max(self.engine.capital, 1e-9)
        return self.total_exposure() / capital

    def can_add(self):
        return self.exposure_ratio() < 0.7
