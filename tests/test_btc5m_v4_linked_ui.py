from __future__ import annotations

import unittest

from polymarket_latency_bot.btc5m_prediction_market_ui_v4_linked import build_dashboard_html_v4


class BTC5mV4LinkedUITests(unittest.TestCase):
    def test_dashboard_inserts_selfcheck_link_once(self) -> None:
        html = build_dashboard_html_v4()
        self.assertIn('<a href="/selfcheck">/selfcheck</a>', html)
        self.assertEqual(html.count('<a href="/selfcheck">/selfcheck</a>'), 1)
        self.assertIn('<a href="/docs">/docs</a>', html)


if __name__ == "__main__":
    unittest.main()
