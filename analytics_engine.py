class AnalyticsEngine:
    def __init__(self, paper):
        self.paper = paper

    def report(self):
        total, by_source = self.paper.stats()

        return {
            "total_pnl": total,
            "by_source": by_source
        }
