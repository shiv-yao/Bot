from __future__ import annotations

import unittest

from polymarket_latency_bot.btc5m_performance import build_paper_analytics


def order(stage: int, edge: float, won: bool, direction: str = "YES", source: str = "multi_source_fusion") -> dict:
    return {
        "scale_stage": stage,
        "net_edge": edge,
        "won": won,
        "direction": direction,
        "signal_source": source,
        "pnl": 1.0 if won else -1.0,
    }


def settled(slug: str, start_ms: int, won: bool, orders: list[dict]) -> dict:
    return {
        "slug": slug,
        "interval_start_ms": start_ms,
        "status": "settled",
        "won": won,
        "orders": orders,
    }


class BTC5mPerformanceTests(unittest.TestCase):
    def test_split_metrics_and_cooldown_recommendation(self) -> None:
        paper = {
            "closed_trades": [
                settled("r1", 1000, False, [order(1, 0.009, False)]),
                settled("r2", 2000, False, [order(2, 0.013, False, direction="NO")]),
                settled("r3", 3000, False, [order(3, 0.020, False)]),
                settled("r4", 4000, True, [order(1, 0.031, True)]),
                settled("r5", 5000, False, [order(1, 0.009, False)]),
                settled("r6", 6000, False, [order(2, 0.013, False, direction="NO")]),
                settled("r7", 7000, False, [order(3, 0.020, False)]),
            ]
        }
        analytics = build_paper_analytics(paper, cooldown_after_losses=3, min_samples_for_review=10)
        self.assertEqual(analytics["samples"], 7)
        self.assertEqual(analytics["sample_status"], "collecting")
        self.assertTrue(analytics["cooldown"]["recommended"])
        self.assertEqual(analytics["cooldown"]["consecutive_round_losses"], 3)
        self.assertEqual(analytics["by_scale_stage"]["stage_1"]["samples"], 3)
        self.assertEqual(analytics["by_scale_stage"]["stage_2"]["losses"], 2)
        self.assertEqual(analytics["by_net_edge"]["0.8_to_1.2pct"]["samples"], 2)
        self.assertEqual(analytics["by_direction"]["NO"]["samples"], 2)
        self.assertFalse(analytics["guidance"]["auto_tuning_enabled"])

    def test_win_breaks_loss_streak(self) -> None:
        paper = {
            "closed_trades": [
                settled("r1", 1000, False, [order(1, 0.010, False)]),
                settled("r2", 2000, True, [order(1, 0.020, True)]),
            ]
        }
        analytics = build_paper_analytics(paper, cooldown_after_losses=2, min_samples_for_review=2)
        self.assertFalse(analytics["cooldown"]["recommended"])
        self.assertEqual(analytics["cooldown"]["consecutive_round_losses"], 0)
        self.assertEqual(analytics["sample_status"], "review_ready")

    def test_review_recommendations_require_enough_group_samples(self) -> None:
        rounds = []
        for index in range(10):
            won = index < 3
            rounds.append(settled(f"r{index}", index, won, [order(2, 0.013, won, direction="NO")]))
        paper = {"closed_trades": rounds}
        analytics = build_paper_analytics(
            paper,
            min_group_samples_for_review=10,
            review_win_rate_threshold=0.45,
        )
        stage_reviews = analytics["review"]["recommendations"]["scale_stage"]
        self.assertEqual(len(stage_reviews), 1)
        self.assertEqual(stage_reviews[0]["group"], "stage_2")
        self.assertEqual(stage_reviews[0]["action"], "review_only")
        self.assertIn("win_rate_below_review_threshold", stage_reviews[0]["reasons"])
        self.assertIn("negative_pnl", stage_reviews[0]["reasons"])
        self.assertFalse(analytics["guidance"]["auto_tuning_enabled"])

    def test_review_does_not_flag_small_groups(self) -> None:
        paper = {
            "closed_trades": [
                settled("r1", 1000, False, [order(3, 0.020, False)]),
                settled("r2", 2000, False, [order(3, 0.020, False)]),
            ]
        }
        analytics = build_paper_analytics(
            paper,
            min_group_samples_for_review=3,
            review_win_rate_threshold=0.99,
        )
        self.assertEqual(analytics["review"]["recommendation_count"], 0)


if __name__ == "__main__":
    unittest.main()
