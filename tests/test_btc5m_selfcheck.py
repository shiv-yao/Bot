from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from polymarket_latency_bot.btc5m_selfcheck import build_selfcheck_payload, register_btc5m_selfcheck


class BTC5mSelfcheckTests(unittest.TestCase):
    def test_manifest_is_v4_hardened_and_paper_only(self) -> None:
        payload = build_selfcheck_payload()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["strategy"], "BTC_5M_EVENT_SCALE_IN_V4_HARDENED")
        self.assertEqual(payload["mode"], "btc_5m_prediction_market_paper_scale_in_v4_hardened")
        self.assertEqual(payload["entrypoint"], "python -m polymarket_latency_bot.btc5m_event_main_v4")
        self.assertEqual(payload["scale_in_weights"], [0.50, 0.30, 0.20])
        self.assertTrue(payload["safety"]["paper_only"])
        self.assertFalse(payload["safety"]["adaptive_cooldown_enabled"])
        self.assertFalse(payload["safety"]["auto_tuning_enabled"])
        self.assertFalse(payload["safety"]["live_orders_enabled"])
        self.assertFalse(payload["safety"]["wallet_signing_enabled"])
        self.assertFalse(payload["safety"]["live_trading_enabled"])
        self.assertTrue(all(payload["quality_gates"].values()))
        self.assertTrue(all(payload["analytics"].values()))
        self.assertTrue(all(payload["checks"].values()))

    def test_selfcheck_endpoint_is_read_only_and_returns_manifest(self) -> None:
        app = FastAPI()
        register_btc5m_selfcheck(app)
        client = TestClient(app)
        response = client.get("/selfcheck")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["execution"], "hardened_three_stage_scale_in_50_30_20")
        self.assertIn("Read-only manifest", payload["note"])


if __name__ == "__main__":
    unittest.main()
