from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from polymarket_latency_bot.evaluation_api import _read_summary, register_evaluation_routes


def create_db(path: str, pnls: list[float]) -> None:
    with sqlite3.connect(path) as db:
        db.execute(
            """
            CREATE TABLE paper_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                realized_pnl REAL NOT NULL
            )
            """
        )
        db.executemany("INSERT INTO paper_trades (realized_pnl) VALUES (?)", [(value,) for value in pnls])


class EvaluationApiTests(unittest.TestCase):
    def test_read_summary_calculates_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "current.db")
            create_db(path, [1.0, -0.5, 0.0, 2.0])
            summary = _read_summary(path)
            self.assertTrue(summary["exists"])
            self.assertEqual(summary["closed_trades"], 4)
            self.assertEqual(summary["wins"], 2)
            self.assertEqual(summary["losses"], 1)
            self.assertEqual(summary["flat"], 1)
            self.assertEqual(summary["win_rate"], round(2 / 3, 6))
            self.assertEqual(summary["net_pnl"], 2.5)
            self.assertEqual(summary["gross_profit"], 3.0)
            self.assertEqual(summary["gross_loss"], 0.5)
            self.assertEqual(summary["profit_factor"], 6.0)

    def test_missing_database_returns_empty_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "missing.db")
            summary = _read_summary(path)
            self.assertFalse(summary["exists"])
            self.assertEqual(summary["closed_trades"], 0)
            self.assertEqual(summary["net_pnl"], 0.0)

    def test_evaluation_status_reports_initial_collection_stage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "current.db")
            create_db(path, [0.1] * 10)
            app = FastAPI()
            portfolio = SimpleNamespace(store=SimpleNamespace(db_path=path))
            register_evaluation_routes(app, portfolio)
            client = TestClient(app)
            response = client.get("/evaluation/status")
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["profile"], "balanced_btc5m_hf")
            self.assertEqual(payload["stage"], "collecting_initial_sample")
            self.assertEqual(payload["current"]["closed_trades"], 10)

    def test_evaluation_dashboard_is_available(self) -> None:
        app = FastAPI()
        portfolio = SimpleNamespace(store=SimpleNamespace(db_path="/tmp/missing.db"))
        register_evaluation_routes(app, portfolio)
        response = TestClient(app).get("/evaluation")
        self.assertEqual(response.status_code, 200)
        self.assertIn("BTC 5m Balanced HF", response.text)


if __name__ == "__main__":
    unittest.main()
