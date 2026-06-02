from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from polymarket_latency_bot.btc5m_round_prediction import BTC5mRoundPredictionEngine
from polymarket_latency_bot.models import BookTop, Prediction
from polymarket_latency_bot.state import BotState


class Settings(SimpleNamespace):
    min_confidence: float = 0.60
    ai_min_probability_margin: float = 0.01
    paper_open_buffer_sec: int = 0
    paper_close_buffer_sec: int = 0
    account_equity_usd: float = 1000.0
    effective_max_order_equity_fraction: float = 0.005
    effective_max_open_notional_usd: float = 10.0


def build_state(*, start_ms: int, probability_up: float, confidence: float) -> BotState:
    state = BotState()
    state.market_discovery_status = "ready"
    state.current_market = {
        "slug": f"btc-updown-5m-{start_ms // 1000}",
        "interval_start": start_ms // 1000,
        "interval_sec": 300,
        "question": "Bitcoin Up or Down - test",
        "yes_token_id": "yes-token",
        "no_token_id": "no-token",
    }
    state.books["yes-token"] = BookTop(token_id="yes-token", best_bid=0.49, best_ask=0.50)
    state.books["no-token"] = BookTop(token_id="no-token", best_bid=0.49, best_ask=0.50)
    state.predictions["multi_source_fusion"] = Prediction(
        source="multi_source_fusion",
        probability_up=probability_up,
        confidence=confidence,
        timestamp_ms=start_ms + 1000,
    )
    state.btc_prices.append((start_ms + 1000, 70000.0))
    return state


class BTC5mRoundPredictionTests(unittest.TestCase):
    def test_same_market_creates_only_one_prediction(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                start_ms = 1_780_000_000_000
                state = build_state(start_ms=start_ms, probability_up=0.70, confidence=0.80)
                engine = BTC5mRoundPredictionEngine(Settings(), state, str(Path(directory) / "rounds.db"))
                await engine.evaluate()
                await engine.evaluate()
                payload = await state.snapshot()
                self.assertEqual(payload["orders_submitted"], 1)
                current = payload["paper_portfolio"]["current_round"]
                self.assertEqual(current["direction"], "YES")
                self.assertEqual(current["status"], "predicted")

        asyncio.run(run())

    def test_yes_prediction_settles_as_win_when_btc_closes_higher(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                start_ms = 1_780_000_000_000
                state = build_state(start_ms=start_ms, probability_up=0.70, confidence=0.80)
                engine = BTC5mRoundPredictionEngine(Settings(), state, str(Path(directory) / "rounds.db"))
                await engine.evaluate()
                state.btc_prices.append((start_ms + 300_000, 70100.0))
                await engine.settle_due_rounds()
                payload = await state.snapshot()
                summary = payload["paper_portfolio"]["summary"]
                self.assertEqual(summary["wins"], 1)
                self.assertEqual(summary["losses"], 0)
                self.assertEqual(summary["closed_trades"], 1)
                self.assertEqual(summary["win_rate"], 1.0)
                closed = payload["paper_portfolio"]["closed_trades"][0]
                self.assertEqual(closed["outcome"], "YES")
                self.assertEqual(closed["reason"], "settled_win")

        asyncio.run(run())

    def test_wait_is_skipped_and_excluded_from_win_rate(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                start_ms = 1_780_000_000_000
                state = build_state(start_ms=start_ms, probability_up=0.70, confidence=0.20)
                engine = BTC5mRoundPredictionEngine(Settings(), state, str(Path(directory) / "rounds.db"))
                await engine.evaluate()
                state.btc_prices.append((start_ms + 300_000, 70100.0))
                await engine.settle_due_rounds()
                payload = await state.snapshot()
                summary = payload["paper_portfolio"]["summary"]
                self.assertEqual(summary["wins"], 0)
                self.assertEqual(summary["losses"], 0)
                self.assertEqual(summary["closed_trades"], 0)
                self.assertEqual(summary["skipped_wait"], 1)
                self.assertEqual(summary["win_rate"], 0.0)
                skipped = payload["paper_portfolio"]["skipped_rounds"][0]
                self.assertEqual(skipped["direction"], "WAIT")
                self.assertEqual(skipped["reason"], "wait_no_prediction")

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
