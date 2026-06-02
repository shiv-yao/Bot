from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from polymarket_latency_bot.btc5m_event_paper_ui import PAPER_DASHBOARD_HTML, register_btc5m_event_paper_ui


class BTC5mEventPaperUITests(unittest.TestCase):
    def test_paper_ui_route_is_available(self) -> None:
        app = FastAPI()
        register_btc5m_event_paper_ui(app)
        response = TestClient(app).get("/ui")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Event Prediction Paper 儀表板", response.text)
        self.assertIn("Paper 模擬持倉", response.text)
        self.assertIn("已實現 PnL", response.text)
        self.assertIn("未實現 PnL", response.text)
        self.assertIn("持倉數", response.text)
        self.assertIn("勝率", response.text)
        self.assertIn("Queue", response.text)

    def test_paper_ui_shows_safe_boundaries(self) -> None:
        self.assertIn("模擬下單", PAPER_DASHBOARD_HTML)
        self.assertIn("持倉模擬", PAPER_DASHBOARD_HTML)
        self.assertIn("真實下單", PAPER_DASHBOARD_HTML)
        self.assertIn("錢包簽名", PAPER_DASHBOARD_HTML)
        self.assertIn("Live Trading", PAPER_DASHBOARD_HTML)
        self.assertIn("/paper/status", PAPER_DASHBOARD_HTML)

    def test_paper_ui_has_no_live_trade_controls(self) -> None:
        self.assertNotIn("/buy", PAPER_DASHBOARD_HTML)
        self.assertNotIn("/sell", PAPER_DASHBOARD_HTML)
        self.assertNotIn("/trade", PAPER_DASHBOARD_HTML)


if __name__ == "__main__":
    unittest.main()
