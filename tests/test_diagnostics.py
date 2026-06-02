from __future__ import annotations

import unittest
from dataclasses import dataclass

from polymarket_latency_bot.diagnostics import build_diagnostics


@dataclass
class Risk:
    halted: bool = False
    halt_reason: str = ""


class Settings:
    external_price_max_age_ms = 3000
    max_queue_size = 1000
    webhook_secret = "safe-secret-value-12345"
    live_enabled = False
    market_interval_sec = 300
    market_slug_prefix = "btc-updown-5m-"


class DiagnosticsTests(unittest.TestCase):
    def healthy_snapshot(self) -> dict:
        return {
            "connections": {"market_ws": True, "rtds_ws": True},
            "source_status": {
                "chainlink": {"connected": True, "age_ms": 100},
                "coinbase": {"connected": True, "age_ms": 100},
            },
            "fusion_snapshot": {"status": "ready"},
            "market_discovery_status": "ready",
            "queue_depth": 0,
            "queue_high_water": 0,
        }

    def test_healthy_snapshot(self) -> None:
        result = build_diagnostics(
            Settings(),
            self.healthy_snapshot(),
            Risk(),
            "/data/polymarket_paper_btc5m_balanced.db",
        )
        self.assertEqual(result["status"], "healthy")
        self.assertEqual(result["warnings"], [])
        self.assertTrue(result["history"]["is_btc5m_isolated"])
        self.assertEqual(result["market"]["interval_minutes"], 5.0)

    def test_warning_for_default_secret_stale_source_and_legacy_db(self) -> None:
        settings = Settings()
        settings.webhook_secret = "change-me"
        snapshot = self.healthy_snapshot()
        snapshot["source_status"] = {"chainlink": {"connected": True, "age_ms": 5000}}
        result = build_diagnostics(settings, snapshot, Risk(), "/tmp/paper.db")
        codes = {item["code"] for item in result["warnings"]}
        self.assertIn("source_stale:chainlink", codes)
        self.assertIn("webhook_secret_not_configured", codes)
        self.assertIn("volume_not_durable", codes)
        self.assertIn("history_not_isolated", codes)

    def test_error_for_non_5m_interval_and_slug(self) -> None:
        settings = Settings()
        settings.market_interval_sec = 900
        settings.market_slug_prefix = "btc-updown-15m-"
        result = build_diagnostics(
            settings,
            self.healthy_snapshot(),
            Risk(),
            "/data/polymarket_paper_btc5m_balanced.db",
        )
        codes = {item["code"] for item in result["warnings"]}
        self.assertEqual(result["status"], "error")
        self.assertIn("market_interval_not_5m", codes)
        self.assertIn("market_slug_not_5m", codes)


if __name__ == "__main__":
    unittest.main()
