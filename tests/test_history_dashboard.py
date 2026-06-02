from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from fastapi import FastAPI

from polymarket_latency_bot.dashboard5m import HTML, register_dashboard5m
from polymarket_latency_bot.history_api import register_history_routes


class HistoryAndDashboardTests(unittest.TestCase):
    def test_history_status_reports_isolated_btc5m_database(self) -> None:
        app = FastAPI()
        settings = SimpleNamespace(
            live_enabled=False,
            market_interval_sec=300,
            market_slug_prefix="btc-updown-5m-",
            paper_high_frequency_profile=True,
        )
        portfolio = SimpleNamespace(
            store=SimpleNamespace(db_path="/data/polymarket_paper_btc5m_balanced.db")
        )
        register_history_routes(app, settings, portfolio)
        route = next(route for route in app.routes if getattr(route, "path", "") == "/history/status")
        result = asyncio.run(route.endpoint())
        self.assertEqual(result["mode"], "paper")
        self.assertEqual(result["profile"], "balanced_btc5m_hf")
        self.assertTrue(result["database"]["is_btc5m_isolated"])
        self.assertEqual(result["market"]["interval_sec"], 300)
        self.assertEqual(result["market"]["interval_minutes"], 5.0)
        self.assertFalse(result["safety"]["live_enabled"])

    def test_dashboard_route_and_copy_are_btc5m(self) -> None:
        app = FastAPI()
        register_dashboard5m(app)
        paths = {getattr(route, "path", "") for route in app.routes}
        self.assertIn("/dashboard5m", paths)
        self.assertIn("BTC 5 分鐘市場", HTML)
        self.assertIn("Balanced HF Paper", HTML)
        self.assertNotIn("BTC 15 分鐘市場", HTML)


if __name__ == "__main__":
    unittest.main()
