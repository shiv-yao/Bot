from __future__ import annotations

import unittest
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from polymarket_latency_bot.history_api import register_history_routes


class HistoryStatusTests(unittest.TestCase):
    def test_reports_isolated_btc5m_paper_history(self) -> None:
        settings = SimpleNamespace(
            live_enabled=False,
            paper_high_frequency_profile=True,
            market_interval_sec=300,
            market_slug_prefix="btc-updown-5m-",
        )
        portfolio = SimpleNamespace(
            store=SimpleNamespace(db_path="/data/polymarket_paper_btc5m_balanced.db")
        )
        app = FastAPI()
        register_history_routes(app, settings, portfolio)
        response = TestClient(app).get("/history/status")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["mode"], "paper")
        self.assertEqual(payload["profile"], "balanced_btc5m_hf")
        self.assertEqual(payload["market"]["interval_sec"], 300)
        self.assertEqual(payload["market"]["interval_minutes"], 5.0)
        self.assertEqual(payload["market"]["slug_prefix"], "btc-updown-5m-")
        self.assertTrue(payload["database"]["is_btc5m_isolated"])
        self.assertEqual(payload["database"]["legacy_database_preserved"], "/data/polymarket_paper.db")
        self.assertFalse(payload["safety"]["live_enabled"])

    def test_marks_legacy_database_as_not_isolated(self) -> None:
        settings = SimpleNamespace(
            live_enabled=False,
            paper_high_frequency_profile=True,
            market_interval_sec=300,
            market_slug_prefix="btc-updown-5m-",
        )
        portfolio = SimpleNamespace(store=SimpleNamespace(db_path="/data/polymarket_paper.db"))
        app = FastAPI()
        register_history_routes(app, settings, portfolio)
        payload = TestClient(app).get("/history/status").json()
        self.assertFalse(payload["database"]["is_btc5m_isolated"])


if __name__ == "__main__":
    unittest.main()
