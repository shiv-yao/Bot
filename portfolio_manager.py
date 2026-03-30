from core.portfolio import PortfolioBook


class PortfolioManagerV26:
    """Backward-compatible manager backed by the new portfolio book."""

    def __init__(self, engine):
        self.engine = engine
        self.book = PortfolioBook(engine)

    def total_exposure(self):
        return self.book.total_exposure()

    def exposure_ratio(self):
        return self.book.exposure_ratio()

    def can_add(self):
        return self.book.can_add(max_ratio=0.7)
