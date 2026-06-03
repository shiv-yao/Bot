from __future__ import annotations

import unittest

from fastapi import FastAPI

from polymarket_latency_bot.btc5m_prediction_market_ui_v4_linked import (
    UI_BUILD,
    _NO_STORE_HEADERS,
    build_dashboard_html_v4,
    register_btc5m_prediction_market_ui_v4,
)


class BTC5mV4LinkedUITests(unittest.TestCase):
    def test_dashboard_inserts_health_links_once(self) -> None:
        html = build_dashboard_html_v4()
        self.assertIn('<a href="/selfcheck">/selfcheck</a>', html)
        self.assertIn('<a href="/runtime-health">/runtime-health</a>', html)
        self.assertEqual(html.count('<a href="/selfcheck">/selfcheck</a>'), 1)
        self.assertEqual(html.count('<a href="/runtime-health">/runtime-health</a>'), 1)
        self.assertIn('<a href="/docs">/docs</a>', html)

    def test_dashboard_contains_source_health_card(self) -> None:
        html = build_dashboard_html_v4()
        for expected in (
            "即時資料健康",
            "資料狀態",
            "最後成功更新",
            "Connected Sources",
            "Clean Fusion Sources",
            "Fusion Status",
            "最舊盤口資料",
            "來源摘要",
            "UI Build",
            UI_BUILD,
        ):
            self.assertIn(expected, html)

    def test_dashboard_distinguishes_active_degraded_stale_and_reconnecting(self) -> None:
        html = build_dashboard_html_v4()
        for expected in (
            "SYSTEM ACTIVE",
            "DEGRADED",
            "STALE DATA",
            "RECONNECTING",
            "OFFLINE",
        ):
            self.assertIn(expected, html)
        self.assertIn("oldestBookAge>5000", html)
        self.assertIn("fusionState!=='ready'", html)
        self.assertIn("cleanSources<2", html)
        self.assertIn("connectedCount<2", html)
        self.assertIn("refreshFailures>=3", html)

    def test_status_is_primary_and_mode_failure_is_tolerated(self) -> None:
        html = build_dashboard_html_v4()
        self.assertIn("const statusResponse=await fetch('/status'", html)
        self.assertIn("if(!statusResponse.ok)throw new Error('status_unavailable')", html)
        self.assertIn("try{const modeResponse=await fetch('/mode'", html)
        self.assertIn("catch(_){m={}}", html)

    def test_ui_route_uses_no_store_headers(self) -> None:
        app = FastAPI()
        register_btc5m_prediction_market_ui_v4(app)
        route = next(route for route in app.routes if getattr(route, "path", None) == "/ui")
        response = route.endpoint()
        self.assertEqual(response.headers["cache-control"], _NO_STORE_HEADERS["Cache-Control"])
        self.assertEqual(response.headers["pragma"], _NO_STORE_HEADERS["Pragma"])
        self.assertEqual(response.headers["expires"], _NO_STORE_HEADERS["Expires"])


if __name__ == "__main__":
    unittest.main()
