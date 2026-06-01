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


class DiagnosticsTests(unittest.TestCase):
    def test_healthy_snapshot(self) -> None:
        snapshot = {
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
        result = build_diagnostics(Settings(), snapshot, Risk(), "/data/polymarket_paper.db")
        self.assertEqual(result["status"], "healthy")
        self.assertEqual(result["warnings"], [])

    def test_warning_for_default_secret_and_stale_source(self) -> None:
        settings = Settings()
        settings.webhook_secret = "change-me"
        snapshot = {
            "connections": {"market_ws": True, "rtds_ws": True},
            "source_status": {"chainlink": {"connected": True, "age_ms": 5000}},
            "fusion_snapshot": {"status": "ready"},
            "market_discovery_status": "ready",
            "queue_depth": 0,
            "queue_high_water": 0,
        }
        result = build_diagnostics(settings, snapshot, Risk(), "/tmp/paper.db")
        codes = {item["code"] for item in result["warnings"]}
        self.assertIn("source_stale:chainlink", codes)
        self.assertIn("webhook_secret_not_configured", codes)
        self.assertIn("volume_not_durable", codes)


if __name__ == "__main__":
    unittest.main()
