from __future__ import annotations

import asyncio
import unittest

from polymarket_latency_bot.config import Settings
from polymarket_latency_bot.models import BookTop, Direction, Prediction, now_ms
from polymarket_latency_bot.state import BotState
from polymarket_latency_bot.strategy import LatencyStrategy


def book(token_id: str) -> BookTop:
    return BookTop(
        token_id=token_id,
        best_bid=0.39,
        best_ask=0.40,
        timestamp_ms=now_ms(),
        bid_levels=[{"price": 0.39, "size": 1000.0}],
        ask_levels=[{"price": 0.40, "size": 1000.0}],
    )


class StrategyDirectionTests(unittest.TestCase):
    def settings(self) -> Settings:
        return Settings(
            yes_token_id="yes",
            no_token_id="no",
            min_edge=0.01,
            min_net_edge=0.0,
            min_confidence=0.5,
            ai_min_probability_margin=0.01,
            signal_cooldown_ms=0,
            strategy_evaluation_interval_ms=25,
            paper_open_buffer_sec=0,
            paper_close_buffer_sec=1,
        )

    def test_ai_selects_yes_only(self) -> None:
        async def run() -> None:
            settings = self.settings()
            state = BotState()
            state.books = {"yes": book("yes"), "no": book("no")}
            state.predictions = {"multi_source_fusion": Prediction("multi_source_fusion", 0.60, 0.9, now_ms())}
            intents = await LatencyStrategy(settings, state).build_intents()
            self.assertEqual(len(intents), 1)
            self.assertEqual(intents[0].direction, Direction.BUY_YES)
        asyncio.run(run())

    def test_ai_selects_no_only(self) -> None:
        async def run() -> None:
            settings = self.settings()
            state = BotState()
            state.books = {"yes": book("yes"), "no": book("no")}
            state.predictions = {"multi_source_fusion": Prediction("multi_source_fusion", 0.40, 0.9, now_ms())}
            intents = await LatencyStrategy(settings, state).build_intents()
            self.assertEqual(len(intents), 1)
            self.assertEqual(intents[0].direction, Direction.BUY_NO)
        asyncio.run(run())

    def test_ai_waits_when_probability_is_too_close(self) -> None:
        async def run() -> None:
            settings = self.settings()
            state = BotState()
            state.books = {"yes": book("yes"), "no": book("no")}
            state.predictions = {"multi_source_fusion": Prediction("multi_source_fusion", 0.505, 0.9, now_ms())}
            intents = await LatencyStrategy(settings, state).build_intents()
            self.assertEqual(intents, [])
            self.assertEqual(state.last_strategy_snapshot.get("reason"), "ai_probability_too_close")
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
