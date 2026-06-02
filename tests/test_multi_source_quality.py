from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from polymarket_latency_bot.multi_source import MultiSourceFusion, _next_reconnect_delay
from polymarket_latency_bot.state import BotState


class FakeFeeds:
    def __init__(self) -> None:
        self.predictions = []

    async def upsert_prediction(self, prediction) -> None:
        self.predictions.append(prediction)


class Settings(SimpleNamespace):
    enable_multi_source_fusion: bool = True
    fusion_source_weight_chainlink: float = 1.0
    fusion_source_weight_binance: float = 1.0
    fusion_source_weight_coinbase: float = 1.0
    external_price_window_sec: int = 20
    external_price_max_age_ms: int = 3000
    fusion_min_sources: int = 2
    fusion_agreement_threshold: float = 0.55
    fusion_probability_scale: float = 40.0
    fusion_base_confidence: float = 0.52
    fusion_outlier_max_deviation_bps: float = 35.0
    fusion_max_dispersion_bps: float = 20.0
    source_reconnect_delay_sec: float = 1.0
    source_reconnect_max_delay_sec: float = 8.0


class MultiSourceQualityTests(unittest.TestCase):
    def test_fusion_ready_when_sources_agree(self) -> None:
        async def run() -> None:
            state = BotState()
            feeds = FakeFeeds()
            fusion = MultiSourceFusion(Settings(), state, feeds)
            base = 1_900_000_000_000
            await fusion.record_price("chainlink", 70000.0, timestamp_ms=base)
            await fusion.record_price("binance", 70001.0, timestamp_ms=base)
            await fusion.record_price("coinbase", 70002.0, timestamp_ms=base)
            await fusion.record_price("chainlink", 70010.0, timestamp_ms=base + 1000)
            await fusion.record_price("binance", 70011.0, timestamp_ms=base + 1000)
            await fusion.record_price("coinbase", 70012.0, timestamp_ms=base + 1000)
            snapshot = state.fusion_snapshot
            self.assertEqual(snapshot["status"], "ready")
            self.assertEqual(snapshot["clean_source_count"], 3)
            self.assertEqual(snapshot["outlier_count"], 0)
            self.assertTrue(feeds.predictions)

        asyncio.run(run())

    def test_outlier_source_is_excluded(self) -> None:
        async def run() -> None:
            state = BotState()
            feeds = FakeFeeds()
            fusion = MultiSourceFusion(Settings(), state, feeds)
            base = 1_900_000_000_000
            for source, price in (("chainlink", 70000.0), ("binance", 70001.0), ("coinbase", 70002.0)):
                await fusion.record_price(source, price, timestamp_ms=base)
            await fusion.record_price("chainlink", 70010.0, timestamp_ms=base + 1000)
            await fusion.record_price("binance", 70011.0, timestamp_ms=base + 1000)
            await fusion.record_price("coinbase", 71000.0, timestamp_ms=base + 1000)
            snapshot = state.fusion_snapshot
            self.assertEqual(snapshot["status"], "ready")
            self.assertEqual(snapshot["clean_source_count"], 2)
            self.assertEqual(snapshot["outlier_count"], 1)
            self.assertEqual(snapshot["outliers"][0]["source"], "coinbase")
            self.assertTrue(state.source_status["coinbase"]["fusion_outlier"])

        asyncio.run(run())

    def test_high_dispersion_blocks_new_prediction_publication(self) -> None:
        async def run() -> None:
            settings = Settings(fusion_outlier_max_deviation_bps=100.0, fusion_max_dispersion_bps=20.0)
            state = BotState()
            feeds = FakeFeeds()
            fusion = MultiSourceFusion(settings, state, feeds)
            base = 1_900_000_000_000
            for source, price in (("chainlink", 70000.0), ("binance", 70001.0), ("coinbase", 70002.0)):
                await fusion.record_price(source, price, timestamp_ms=base)
            await fusion.record_price("chainlink", 70010.0, timestamp_ms=base + 1000)
            await fusion.record_price("binance", 70011.0, timestamp_ms=base + 1000)
            published_before_spike = len(feeds.predictions)
            await fusion.record_price("coinbase", 70200.0, timestamp_ms=base + 1000)
            snapshot = state.fusion_snapshot
            self.assertEqual(snapshot["status"], "price_dispersion_high")
            self.assertGreater(snapshot["dispersion_bps"], snapshot["max_dispersion_bps"])
            self.assertEqual(len(feeds.predictions), published_before_spike)

        asyncio.run(run())

    def test_reconnect_delay_uses_bounded_exponential_backoff(self) -> None:
        settings = Settings()
        self.assertEqual(_next_reconnect_delay(settings, 1.0), 2.0)
        self.assertEqual(_next_reconnect_delay(settings, 4.0), 8.0)
        self.assertEqual(_next_reconnect_delay(settings, 8.0), 8.0)


if __name__ == "__main__":
    unittest.main()
