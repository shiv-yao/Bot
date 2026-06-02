from __future__ import annotations

import unittest

from polymarket_latency_bot.btc5m_performance import build_paper_analytics


def order(
    stage: int,
    edge: float,
    won: bool,
    direction: str = "YES",
    source: str = "multi_source_fusion",
    probability: float | None = None,
    created_ms: int = 0,
) -> dict:
    row = {
        "scale_stage": stage,
        "net_edge": edge,
        "won": won,
        "direction": direction,
        "signal_source": source,
        "pnl": 1.0 if won else -1.0,
        "created_ms": created_ms,
    }
    if probability is not None:
        row["expected_probability"] = probability
    return row


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
        analytics = build_paper_analytics(
            {"closed_trades": rounds},
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

    def test_calibration_detects_overconfidence_and_rolling_window(self) -> None:
        rounds = []
        for index in range(10):
            won = index < 2
            rows = [order(1, 0.020, won, probability=0.85, created_ms=index)]
            rounds.append(settled(f"r{index}", index, won, rows))
        analytics = build_paper_analytics(
            {"closed_trades": rounds},
            rolling_window=5,
            calibration_min_bucket_samples=10,
            overconfidence_gap_threshold=0.10,
        )
        calibration = analytics["calibration"]
        self.assertEqual(analytics["rolling"]["samples"], 5)
        self.assertEqual(analytics["rolling"]["wins"], 0)
        self.assertAlmostEqual(calibration["average_expected_probability"], 0.85)
        self.assertAlmostEqual(calibration["observed_win_rate"], 0.20)
        self.assertAlmostEqual(calibration["brier_score"], 0.5825)
        self.assertEqual(len(calibration["overconfidence_reviews"]), 1)
        self.assertEqual(calibration["overconfidence_reviews"][0]["reason"], "overconfidence_gap")
        self.assertFalse(analytics["guidance"]["auto_tuning_enabled"])


if __name__ == "__main__":
    unittest.main()
