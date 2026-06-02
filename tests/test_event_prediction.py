from __future__ import annotations

import asyncio
import unittest
from dataclasses import asdict

from fastapi import FastAPI
from fastapi.testclient import TestClient

from polymarket_latency_bot.event_prediction import (
    EventCandidate,
    EventPredictionEngine,
    ForecastIn,
    register_event_prediction_routes,
)
from polymarket_latency_bot.models import now_ms


class EventPredictionTests(unittest.TestCase):
    def engine(self) -> EventPredictionEngine:
        engine = EventPredictionEngine("https://gamma-api.polymarket.com", "safe-secret-value-12345")
        engine.markets = {
            "sample-event": EventCandidate(
                event_slug="sample-parent",
                slug="sample-event",
                question="Will the sample event happen?",
                condition_id="condition",
                yes_token_id="yes-token",
                no_token_id="no-token",
                market_yes_price=0.40,
                market_no_price=0.60,
                liquidity=5000.0,
                volume_24hr=2500.0,
                end_date_ms=None,
                scanned_ms=now_ms(),
            )
        }
        return engine

    def test_buy_yes_signal(self) -> None:
        async def run() -> None:
            engine = self.engine()
            await engine.upsert_forecast(ForecastIn(
                market_slug="sample-event",
                probability_yes=0.62,
                confidence=0.80,
                source="unit_test",
            ))
            signals = await engine.signals(10)
            self.assertEqual(len(signals), 1)
            self.assertEqual(signals[0]["direction"], "BUY_YES")
            self.assertAlmostEqual(signals[0]["yes_edge"], 0.22)
        asyncio.run(run())

    def test_buy_no_signal(self) -> None:
        async def run() -> None:
            engine = self.engine()
            await engine.upsert_forecast(ForecastIn(
                market_slug="sample-event",
                probability_yes=0.20,
                confidence=0.85,
                source="unit_test",
            ))
            signals = await engine.signals(10)
            self.assertEqual(signals[0]["direction"], "BUY_NO")
            self.assertAlmostEqual(signals[0]["no_edge"], 0.20)
        asyncio.run(run())

    def test_wait_when_confidence_is_low(self) -> None:
        async def run() -> None:
            engine = self.engine()
            await engine.upsert_forecast(ForecastIn(
                market_slug="sample-event",
                probability_yes=0.80,
                confidence=0.40,
                source="unit_test",
            ))
            signals = await engine.signals(10)
            self.assertEqual(signals[0]["direction"], "WAIT")
        asyncio.run(run())

    def test_status_and_market_routes(self) -> None:
        engine = self.engine()
        app = FastAPI()
        register_event_prediction_routes(app, engine)
        client = TestClient(app)

        status = client.get("/event-prediction/status")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["engine"], "event_prediction")
        self.assertEqual(status.json()["mode"], "paper")

        markets = client.get("/event-prediction/markets")
        self.assertEqual(markets.status_code, 200)
        self.assertEqual(markets.json()["count"], 1)
        self.assertEqual(markets.json()["markets"][0]["slug"], "sample-event")

        ui = client.get("/event-prediction/ui")
        self.assertEqual(ui.status_code, 200)
        self.assertIn("Event Prediction Paper", ui.text)

    def test_prediction_write_requires_configured_secret(self) -> None:
        engine = EventPredictionEngine("https://gamma-api.polymarket.com", "change-me")
        app = FastAPI()
        register_event_prediction_routes(app, engine)
        client = TestClient(app)
        response = client.post(
            "/event-prediction/prediction",
            headers={"X-Webhook-Secret": "anything"},
            json={"market_slug": "sample-event", "probability_yes": 0.70},
        )
        self.assertEqual(response.status_code, 503)

    def test_prediction_write_accepts_valid_secret(self) -> None:
        engine = self.engine()
        app = FastAPI()
        register_event_prediction_routes(app, engine)
        client = TestClient(app)
        response = client.post(
            "/event-prediction/prediction",
            headers={"X-Webhook-Secret": "safe-secret-value-12345"},
            json={
                "market_slug": "sample-event",
                "probability_yes": 0.70,
                "confidence": 0.75,
                "source": "unit_test",
                "rationale": "test rationale",
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["accepted"])
        self.assertEqual(payload["forecast"]["market_slug"], "sample-event")


if __name__ == "__main__":
    unittest.main()
