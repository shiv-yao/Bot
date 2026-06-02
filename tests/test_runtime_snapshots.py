from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from polymarket_latency_bot.runtime_snapshots import RuntimeSnapshotRecorder, RuntimeSnapshotStore, register_snapshot_routes


class FakeState:
    async def snapshot(self) -> dict:
        return {
            "current_market": {"slug": "btc-updown-5m-123"},
            "last_strategy_snapshot": {
                "direction": "BUY_YES",
                "decision": "accepted",
                "fair_probability_up": 0.61,
                "confidence": 0.72,
            },
            "paper_portfolio": {
                "summary": {
                    "realized_pnl": 1.25,
                    "unrealized_pnl": 0.5,
                    "wins": 3,
                    "losses": 1,
                }
            },
            "queue_depth": 4,
            "queue_high_water": 9,
            "throughput": {"strategy_intent": {"last_60s": 12, "per_sec": 0.2}},
            "latency": {"strategy_ms": {"p95_ms": 3.0}},
            "connections": {"market_ws": True, "rtds_ws": True},
            "fusion_snapshot": {"status": "ready"},
        }


class FakeRisk:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.snapshot = SimpleNamespace(
            day_start_equity=1000.0,
            realized_pnl=1.25,
            open_notional=5.0,
            halted=False,
            halt_reason="",
        )


class RuntimeSnapshotTests(unittest.TestCase):
    def test_store_records_and_reads_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = str(Path(directory) / "paper.db")
            store = RuntimeSnapshotStore(db_path)
            store.record({
                "timestamp_ms": 123,
                "market": {"slug": "btc-updown-5m-123"},
                "ai": {"direction": "BUY_YES"},
                "paper": {"realized_pnl": 1.0, "unrealized_pnl": 0.5},
                "risk": {"halted": False},
                "queue_depth": 2,
            })
            self.assertEqual(store.count(), 1)
            recent = store.recent(10)
            self.assertEqual(len(recent), 1)
            self.assertEqual(recent[0]["ai"]["direction"], "BUY_YES")
            self.assertEqual(recent[0]["market"]["slug"], "btc-updown-5m-123")

    def test_capture_and_routes(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                db_path = str(Path(directory) / "paper.db")
                portfolio = SimpleNamespace(store=SimpleNamespace(db_path=db_path))
                recorder = RuntimeSnapshotRecorder(FakeState(), FakeRisk(), portfolio, interval_sec=60)
                payload = await recorder.capture()
                self.assertEqual(payload["ai"]["direction"], "BUY_YES")
                self.assertEqual(payload["paper"]["realized_pnl"], 1.25)
                self.assertEqual(recorder.store.count(), 1)

                app = FastAPI()
                register_snapshot_routes(app, recorder)
                client = TestClient(app)

                profile = client.get("/profile/status")
                self.assertEqual(profile.status_code, 200)
                self.assertEqual(profile.json()["profile"]["name"], "balanced_btc5m_hf")

                status = client.get("/snapshots/status")
                self.assertEqual(status.status_code, 200)
                self.assertEqual(status.json()["snapshot_count"], 1)

                recent = client.get("/snapshots/recent?limit=5")
                self.assertEqual(recent.status_code, 200)
                self.assertEqual(len(recent.json()["snapshots"]), 1)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
