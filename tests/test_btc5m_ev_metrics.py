from __future__ import annotations

import unittest

from polymarket_latency_bot.btc5m_ev_metrics import build_ev_metrics


class BTC5mEVMetricsTests(unittest.TestCase):
    def test_ev_profit_factor_and_drawdown(self) -> None:
        paper = {
            "closed_trades": [
                {
                    "status": "settled",
                    "orders": [
                        {
                            "created_ms": 1,
                            "entry_price": 0.50,
                            "shares": 20.0,
                            "notional_usd": 10.0,
                            "expected_probability": 0.60,
                            "net_edge": 0.08,
                            "won": True,
                            "pnl": 10.0,
                        },
                        {
                            "created_ms": 2,
                            "entry_price": 0.50,
                            "shares": 20.0,
                            "notional_usd": 10.0,
                            "expected_probability": 0.55,
                            "net_edge": 0.03,
                            "won": False,
                            "pnl": -10.0,
                        },
                        {
                            "created_ms": 3,
                            "entry_price": 0.40,
                            "shares": 25.0,
                            "notional_usd": 10.0,
                            "expected_probability": 0.60,
                            "net_edge": 0.18,
                            "won": True,
                            "pnl": 15.0,
                        },
                    ],
                }
            ]
        }
        metrics = build_ev_metrics(paper)
        self.assertEqual(metrics["samples"], 3)
        self.assertEqual(metrics["total_notional_usd"], 30.0)
        self.assertEqual(metrics["realized_pnl"], 15.0)
        self.assertAlmostEqual(metrics["realized_ev"], 0.5)
        self.assertEqual(metrics["gross_profit"], 25.0)
        self.assertEqual(metrics["gross_loss"], 10.0)
        self.assertEqual(metrics["profit_factor"], 2.5)
        self.assertEqual(metrics["maximum_drawdown"], 10.0)
        self.assertAlmostEqual(metrics["average_entry_price"], 0.46666667)
        self.assertAlmostEqual(metrics["average_net_edge"], 0.09666667)

    def test_empty_portfolio_returns_observational_defaults(self) -> None:
        metrics = build_ev_metrics({})
        self.assertEqual(metrics["samples"], 0)
        self.assertEqual(metrics["total_notional_usd"], 0.0)
        self.assertIsNone(metrics["realized_ev"])
        self.assertIsNone(metrics["expected_ev"])
        self.assertEqual(metrics["maximum_drawdown"], 0.0)


if __name__ == "__main__":
    unittest.main()
