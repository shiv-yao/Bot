from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from polymarket_latency_bot.persistence import PaperStore


@dataclass
class Trade:
    token_id: str
    direction: str
    notional_usd: float
    entry_price: float
    exit_price: float
    shares: float
    realized_pnl: float
    opened_ms: int
    closed_ms: int
    hold_ms: int
    close_reason: str
    market_slug: str
    order_count: int = 1


class PersistenceTests(unittest.TestCase):
    def test_performance_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = PaperStore(str(Path(directory) / "paper.db"))
            store.record_trade(Trade("a", "BUY_YES", 5, 0.5, 0.6, 10, 1.0, 1, 11, 10, "take_profit", "m1"))
            store.record_trade(Trade("b", "BUY_NO", 5, 0.5, 0.45, 10, -0.5, 20, 40, 20, "stop_loss", "m2"))
            result = store.performance()
            self.assertEqual(result["closed_trades"], 2)
            self.assertEqual(result["net_pnl"], 0.5)
            self.assertEqual(result["gross_profit"], 1.0)
            self.assertEqual(result["gross_loss"], 0.5)
            self.assertEqual(result["profit_factor"], 2.0)
            self.assertEqual(result["max_drawdown"], 0.5)
            self.assertEqual(result["average_hold_ms"], 15.0)


if __name__ == "__main__":
    unittest.main()
