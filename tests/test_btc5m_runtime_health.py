from __future__ import annotations

import unittest

from fastapi import FastAPI

from polymarket_latency_bot.btc5m_runtime_health import (
    build_runtime_health,
    get_runtime_health,
    register_btc5m_runtime_health,
    update_runtime_health,
)


def payload(*, discovery_status: str = "ready", fusion_status: str = "ready", clean_sources: int = 2, connected_sources: int = 2, yes_book_age_ms: int = 1000, no_book_age_ms: int = 1200) -> dict:
    sources = {
        f"source-{index}": {"connected": index < connected_sources, "last_error": None}
        for index in range(3)
    }
    return {
        "strategy": "BTC_5M_EVENT_SCALE_IN_V4_HARDENED",
        "mode": "btc_5m_prediction_market_paper_scale_in_v4_hardened",
        "market": {
            "discovery_status": discovery_status,
            "yes_book_age_ms": yes_book_age_ms,
            "no_book_age_ms": no_book_age_ms,
        },
        "fusion": {
            "status": fusion_status,
            "clean_source_count": clean_sources,
        },
        "sources": sources,
    }


class BTC5mRuntimeHealthTests(unittest.TestCase):
    def test_active_when_market_sources_fusion_and_books_are_fresh(self) -> None:
        health = build_runtime_health(payload(), timestamp_ms=1_900_000_000_000)
        self.assertTrue(health["ok"])
        self.assertEqual(health["status"], "active")
        self.assertTrue(health["market_ready"])
        self.assertEqual(health["connected_sources"], 2)
        self.assertEqual(health["clean_sources"], 2)
        self.assertEqual(health["fusion_status"], "ready")
        self.assertEqual(health["oldest_book_age_ms"], 1200)

    def test_degraded_when_sources_or_fusion_are_insufficient(self) -> None:
        health = build_runtime_health(
            payload(fusion_status="waiting_for_sources", clean_sources=1, connected_sources=1),
            timestamp_ms=1_900_000_000_000,
        )
        self.assertFalse(health["ok"])
        self.assertEqual(health["status"], "degraded")
        self.assertEqual(health["connected_sources"], 1)
        self.assertEqual(health["clean_sources"], 1)
        self.assertEqual(health["fusion_status"], "waiting_for_sources")

    def test_stale_data_has_priority_over_degraded(self) -> None:
        health = build_runtime_health(
            payload(fusion_status="waiting_for_sources", clean_sources=1, connected_sources=1, yes_book_age_ms=7000),
            timestamp_ms=1_900_000_000_000,
        )
        self.assertFalse(health["ok"])
        self.assertEqual(health["status"], "stale_data")
        self.assertEqual(health["oldest_book_age_ms"], 7000)
        self.assertEqual(health["stale_book_threshold_ms"], 5000)

    def test_update_and_get_return_latest_snapshot(self) -> None:
        latest = update_runtime_health(payload())
        self.assertEqual(get_runtime_health(), latest)

    def test_runtime_health_route_is_registered_as_get(self) -> None:
        app = FastAPI()
        register_btc5m_runtime_health(app)
        route = next(route for route in app.routes if getattr(route, "path", None) == "/runtime-health")
        self.assertIn("GET", route.methods)
        self.assertEqual(route.name, "runtime_health")


if __name__ == "__main__":
    unittest.main()
