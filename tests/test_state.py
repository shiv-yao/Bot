from __future__ import annotations

import asyncio
import unittest

from polymarket_latency_bot.state import BotState


class BotStateTests(unittest.TestCase):
    def test_latency_and_throughput_snapshot(self) -> None:
        async def run() -> None:
            state = BotState()
            await state.record_latency("strategy_ms", 1.0)
            await state.record_latency("strategy_ms", 3.0)
            await state.record_latency("strategy_ms", 2.0)
            await state.record_event("strategy_evaluation")
            await state.record_event("strategy_evaluation")
            async with state.lock:
                state.queue_depth = 4
                state.queue_high_water = 7
            snapshot = await state.snapshot()
            latency = snapshot["latency"]["strategy_ms"]
            self.assertEqual(latency["count"], 3)
            self.assertEqual(latency["p50_ms"], 2.0)
            self.assertEqual(snapshot["throughput"]["strategy_evaluation"]["last_60s"], 2)
            self.assertEqual(snapshot["queue_depth"], 4)
            self.assertEqual(snapshot["queue_high_water"], 7)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
