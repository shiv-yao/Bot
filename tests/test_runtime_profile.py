from __future__ import annotations

import unittest

from polymarket_latency_bot.config import Settings
from polymarket_latency_bot.runtime_profile import apply_balanced_btc5m_paper_profile


class RuntimeProfileTests(unittest.TestCase):
    def test_paper_profile_forces_btc_5m_and_isolated_history(self) -> None:
        settings = Settings(
            live_trading=False,
            market_slug_prefix="btc-updown-15m-",
            market_interval_sec=900,
            paper_db_path="/data/polymarket_paper.db",
        )
        apply_balanced_btc5m_paper_profile(settings)
        self.assertEqual(settings.market_slug_prefix, "btc-updown-5m-")
        self.assertEqual(settings.market_interval_sec, 300)
        self.assertEqual(settings.paper_db_path, "/data/polymarket_paper_btc5m_balanced.db")
        self.assertEqual(settings.signal_cooldown_ms, 500)
        self.assertEqual(settings.strategy_evaluation_interval_ms, 75)
        self.assertEqual(settings.execution_workers, 4)
        self.assertEqual(settings.max_queue_size, 2000)
        self.assertEqual(settings.paper_hf_max_open_notional_usd, 30.0)

    def test_live_mode_is_not_overridden_by_paper_profile(self) -> None:
        settings = Settings(
            live_trading=True,
            live_confirmation="I_UNDERSTAND_LIVE_ORDERS",
            pk="test-key",
            funder_address="0x0000000000000000000000000000000000000001",
            auto_discover_market=True,
            market_slug_prefix="btc-updown-5m-",
            market_interval_sec=300,
        )
        before_db = settings.paper_db_path
        before_cooldown = settings.signal_cooldown_ms
        apply_balanced_btc5m_paper_profile(settings)
        self.assertTrue(settings.live_enabled)
        self.assertEqual(settings.paper_db_path, before_db)
        self.assertEqual(settings.signal_cooldown_ms, before_cooldown)


if __name__ == "__main__":
    unittest.main()
