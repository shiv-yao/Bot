from __future__ import annotations

import unittest

from polymarket_latency_bot import btc5m_event_main as legacy
from polymarket_latency_bot import btc5m_event_main_v4 as v4
from polymarket_latency_bot.btc5m_analytics_v4 import build_paper_analytics
from polymarket_latency_bot.btc5m_prediction_market_ui_v4 import DASHBOARD_HTML_V4


class BTC5mV4IntegrationTests(unittest.TestCase):
    def test_launcher_exposes_v4_hardened_mode(self) -> None:
        payload = v4.build_mode_status()
        self.assertEqual(legacy.STRATEGY_NAME, "BTC_5M_EVENT_SCALE_IN_V4_HARDENED")
        self.assertEqual(legacy.MODE_NAME, "btc_5m_prediction_market_paper_scale_in_v4_hardened")
        self.assertEqual(payload["execution"], "hardened_three_stage_scale_in_50_30_20")
        self.assertTrue(payload["rules"]["require_persistent_stage_confirmation"])
        self.assertTrue(payload["rules"]["require_clean_sources_by_stage"])
        self.assertTrue(payload["rules"]["require_fusion_for_later_scale_in"])
        self.assertTrue(payload["rules"]["require_book_imbalance"])
        self.assertTrue(payload["rules"]["prevent_price_chasing"])
        self.assertTrue(payload["rules"]["prevent_edge_decay"])
        self.assertTrue(payload["rules"]["validate_btc_open_close_quality"])
        self.assertTrue(payload["rules"]["shadow_ab_enabled"])
        self.assertFalse(payload["rules"]["adaptive_cooldown"])
        self.assertFalse(payload["safety"]["adaptive_cooldown_enabled"])

    def test_v4_analytics_adds_ev_shadow_and_data_quality(self) -> None:
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
                            "scale_stage": 1,
                            "direction": "YES",
                            "signal_source": "multi_source_fusion",
                        }
                    ],
                }
            ],
            "skipped_rounds": [
                {"reason": "invalid_btc_close_delayed"},
            ],
            "shadow_ab": {
                "enabled": True,
                "profiles": {"baseline": {"settled_orders": 1, "realized_ev": 0.10}},
            },
        }
        analytics = build_paper_analytics(paper)
        self.assertIn("ev", analytics)
        self.assertIn("shadow_ab", analytics)
        self.assertIn("data_quality", analytics)
        self.assertEqual(analytics["ev"]["samples"], 1)
        self.assertEqual(analytics["shadow_ab"]["profiles"]["baseline"]["settled_orders"], 1)
        self.assertEqual(analytics["data_quality"]["invalid_rounds"], 1)
        self.assertEqual(analytics["data_quality"]["invalid_by_reason"]["invalid_btc_close_delayed"], 1)

    def test_v4_mobile_dashboard_contains_hardened_fields(self) -> None:
        for expected in (
            "V4 Hardened",
            "Realized EV",
            "Profit Factor",
            "最大回撤",
            "Shadow A/B",
            "Invalid BTC Data",
            "Book Imbalance",
            "Stage Confirmation",
            "Adaptive Cooldown",
        ):
            self.assertIn(expected, DASHBOARD_HTML_V4)


if __name__ == "__main__":
    unittest.main()
