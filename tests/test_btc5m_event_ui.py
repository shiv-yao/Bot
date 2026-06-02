from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from polymarket_latency_bot.btc5m_event_ui import DASHBOARD_HTML, register_btc5m_event_ui


class BTC5mEventUITests(unittest.TestCase):
    def test_ui_route_is_available(self) -> None:
        app = FastAPI()
        register_btc5m_event_ui(app)
        response = TestClient(app).get("/ui")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Event Prediction 專用儀表板", response.text)
        self.assertIn("YES / NO / WAIT", response.text)
        self.assertIn("距離本輪結束", response.text)
        self.assertIn("系統安全模式", response.text)

    def test_ui_contains_read_only_endpoints(self) -> None:
        self.assertIn("/status", DASHBOARD_HTML)
        self.assertIn("/mode", DASHBOARD_HTML)
        self.assertIn("/healthz", DASHBOARD_HTML)
        self.assertIn("/docs", DASHBOARD_HTML)

    def test_ui_does_not_include_live_trade_controls(self) -> None:
        self.assertNotIn("/buy", DASHBOARD_HTML)
        self.assertNotIn("/sell", DASHBOARD_HTML)
        self.assertNotIn("/trade", DASHBOARD_HTML)
        self.assertNotIn("private_key", DASHBOARD_HTML.lower())
        self.assertNotIn("seed phrase", DASHBOARD_HTML.lower())

    def test_ui_explicitly_shows_wallet_signing_is_off(self) -> None:
        self.assertIn("錢包簽名", DASHBOARD_HTML)
        self.assertIn('id="wallet">OFF', DASHBOARD_HTML)


if __name__ == "__main__":
    unittest.main()
