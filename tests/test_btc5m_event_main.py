from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from polymarket_latency_bot.btc5m_event_main import MODE_NAME, STRATEGY_NAME, build_mode_status, build_status
from polymarket_latency_bot.state import BotState


class Settings(SimpleNamespace):
    yes_token_id: str = "yes-token"
    no_token_id: str = "no-token"
    min_confidence: float = 0.56
    min_edge: float = 0.02
    ai_min_probability_margin: float = 0.006


class BTC5mEventPredictionTests(unittest.TestCase):
    def test_mode_status_is_guarded_paper_scale_in(self) -> None:
        payload = build_mode_status()
        self.assertEqual(payload["mode"], MODE_NAME)
        self.assertEqual(payload["strategy"], STRATEGY_NAME)
        self.assertEqual(payload["execution"], "guarded_three_stage_scale_in_50_30_20")
        self.assertEqual(payload["outputs"], ["YES", "NO", "WAIT"])
        self.assertEqual(payload["rules"]["scale_in_weights"], [0.50, 0.30, 0.20])
        self.assertEqual(payload["rules"]["max_entries_per_market"], 3)
        self.assertTrue(payload["rules"]["require_same_direction_revalidation"])
        self.assertTrue(payload["rules"]["require_fresh_signal"])
        self.assertTrue(payload["rules"]["require_fresh_book"])
        self.assertTrue(payload["rules"]["require_net_edge"])
        self.assertTrue(payload["rules"]["require_book_depth"])
        self.assertTrue(payload["safety"]["paper_predictions_enabled"])
        self.assertTrue(payload["safety"]["paper_orders_enabled"])
        self.assertTrue(payload["safety"]["paper_positions_enabled"])
        self.assertTrue(payload["safety"]["scale_in_enabled"])
        self.assertTrue(payload["safety"]["paper_only"])
        self.assertFalse(payload["safety"]["live_orders_enabled"])
        self.assertFalse(payload["safety"]["general_event_scanner_enabled"])
        self.assertFalse(payload["safety"]["wallet_signing_enabled"])
        self.assertFalse(payload["safety"]["live_trading_enabled"])

    def test_wait_without_books(self) -> None:
        async def run() -> None:
            state = BotState()
            state.predictions["multi_source_fusion"] = SimpleNamespace(
                probability_up=0.62,
                confidence=0.80,
                timestamp_ms=1,
                to_dict=lambda: {"probability_up": 0.62, "confidence": 0.80, "timestamp_ms": 1},
            )
            payload = await build_status(Settings(), state)
            self.assertEqual(payload["mode"], MODE_NAME)
            self.assertEqual(payload["execution"], "guarded_three_stage_scale_in_50_30_20")
            self.assertEqual(payload["ai"]["direction"], "WAIT")
            self.assertEqual(payload["ai"]["preview_reason"], "waiting_for_order_book")
            self.assertIn("paper", payload)
            self.assertIn("execution_metrics", payload)

        asyncio.run(run())

    def test_yes_when_yes_edge_is_best(self) -> None:
        async def run() -> None:
            state = BotState()
            state.predictions["multi_source_fusion"] = SimpleNamespace(
                probability_up=0.62,
                confidence=0.80,
                timestamp_ms=1,
                to_dict=lambda: {"probability_up": 0.62, "confidence": 0.80, "timestamp_ms": 1},
            )
            state.books["yes-token"] = SimpleNamespace(
                to_dict=lambda: {"best_bid": 0.39, "best_ask": 0.40}
            )
            state.books["no-token"] = SimpleNamespace(
                to_dict=lambda: {"best_bid": 0.59, "best_ask": 0.60}
            )
            payload = await build_status(Settings(), state)
            self.assertEqual(payload["ai"]["direction"], "YES")
            self.assertEqual(payload["ai"]["preview_reason"], "preview_yes_edge")
            self.assertAlmostEqual(payload["ai"]["yes_edge"], 0.22)

        asyncio.run(run())

    def test_no_when_no_edge_is_best(self) -> None:
        async def run() -> None:
            state = BotState()
            state.predictions["multi_source_fusion"] = SimpleNamespace(
                probability_up=0.20,
                confidence=0.85,
                timestamp_ms=1,
                to_dict=lambda: {"probability_up": 0.20, "confidence": 0.85, "timestamp_ms": 1},
            )
            state.books["yes-token"] = SimpleNamespace(
                to_dict=lambda: {"best_bid": 0.39, "best_ask": 0.40}
            )
            state.books["no-token"] = SimpleNamespace(
                to_dict=lambda: {"best_bid": 0.59, "best_ask": 0.60}
            )
            payload = await build_status(Settings(), state)
            self.assertEqual(payload["ai"]["direction"], "NO")
            self.assertEqual(payload["ai"]["preview_reason"], "preview_no_edge")
            self.assertAlmostEqual(payload["ai"]["no_edge"], 0.20)

        asyncio.run(run())

    def test_wait_when_confidence_is_low(self) -> None:
        async def run() -> None:
            state = BotState()
            state.predictions["multi_source_fusion"] = SimpleNamespace(
                probability_up=0.70,
                confidence=0.20,
                timestamp_ms=1,
                to_dict=lambda: {"probability_up": 0.70, "confidence": 0.20, "timestamp_ms": 1},
            )
            state.books["yes-token"] = SimpleNamespace(
                to_dict=lambda: {"best_bid": 0.39, "best_ask": 0.40}
            )
            state.books["no-token"] = SimpleNamespace(
                to_dict=lambda: {"best_bid": 0.59, "best_ask": 0.60}
            )
            payload = await build_status(Settings(), state)
            self.assertEqual(payload["ai"]["direction"], "WAIT")
            self.assertEqual(payload["ai"]["preview_reason"], "confidence_too_low")

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
