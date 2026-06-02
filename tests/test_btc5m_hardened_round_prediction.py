from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from polymarket_latency_bot.btc5m_adaptive_engine import BTC5mAdaptiveRoundPredictionEngine
from polymarket_latency_bot.models import BookTop, Prediction
from polymarket_latency_bot.state import BotState


class Settings(SimpleNamespace):
    min_confidence: float = 0.60
    ai_min_probability_margin: float = 0.01
    paper_open_buffer_sec: int = 0
    paper_close_buffer_sec: int = 0
    account_equity_usd: float = 1000.0
    effective_max_order_equity_fraction: float = 0.005
    effective_max_open_notional_usd: float = 100.0
    max_signal_age_ms: int = 1200
    min_contract_price: float = 0.10
    max_contract_price: float = 0.90
    max_spread: float = 0.06
    slippage_buffer: float = 0.002
    min_depth_multiple: float = 1.25
    depth_levels: int = 5


def set_books(state: BotState, *, timestamp_ms: int, yes_ask: float = 0.50, no_ask: float = 0.50, yes_bid: float | None = 0.49) -> None:
    state.books["yes-token"] = BookTop(
        token_id="yes-token",
        best_bid=yes_bid,
        best_ask=yes_ask,
        timestamp_ms=timestamp_ms,
        ask_levels=[{"price": yes_ask, "size": 1000.0}],
        bid_levels=[] if yes_bid is None else [{"price": yes_bid, "size": 1000.0}],
    )
    state.books["no-token"] = BookTop(
        token_id="no-token",
        best_bid=max(0.01, no_ask - 0.01),
        best_ask=no_ask,
        timestamp_ms=timestamp_ms,
        ask_levels=[{"price": no_ask, "size": 1000.0}],
        bid_levels=[{"price": max(0.01, no_ask - 0.01), "size": 1000.0}],
    )


def refresh_signal(state: BotState, *, timestamp_ms: int, probability_up: float = 0.70, confidence: float = 0.80, yes_bid: float | None = 0.49) -> None:
    state.predictions["multi_source_fusion"] = Prediction(
        source="multi_source_fusion",
        probability_up=probability_up,
        confidence=confidence,
        timestamp_ms=timestamp_ms,
    )
    set_books(state, timestamp_ms=timestamp_ms, yes_bid=yes_bid)


def build_state(*, start_ms: int) -> BotState:
    state = BotState()
    state.market_discovery_status = "ready"
    state.current_market = {
        "slug": f"btc-updown-5m-{start_ms // 1000}",
        "interval_start": start_ms // 1000,
        "interval_sec": 300,
        "question": "Bitcoin Up or Down - hardened test",
        "yes_token_id": "yes-token",
        "no_token_id": "no-token",
    }
    refresh_signal(state, timestamp_ms=start_ms + 1000)
    state.btc_prices.append((start_ms + 1000, 70000.0))
    return state


class BTC5mHardenedRoundPredictionTests(unittest.TestCase):
    def test_missing_best_bid_is_rejected(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                start_ms = 1_900_000_000_000
                state = build_state(start_ms=start_ms)
                refresh_signal(state, timestamp_ms=start_ms + 1000, yes_bid=None)
                env = {"BTC5M_PAPER_CLOSE_BUFFER_SEC": "0"}
                with patch.dict("os.environ", env, clear=False):
                    engine = BTC5mAdaptiveRoundPredictionEngine(Settings(), state, str(Path(directory) / "rounds.db"))
                with patch("polymarket_latency_bot.btc5m_hardened_round_prediction.now_ms", return_value=start_ms + 1000):
                    await engine.evaluate()
                payload = await state.snapshot()
                current = payload["paper_portfolio"]["current_round"]
                self.assertEqual(payload["orders_submitted"], 0)
                self.assertEqual(current["reason"], "best_bid_missing")

        asyncio.run(run())

    def test_stage_two_requires_clean_fusion_sources(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                start_ms = 1_900_000_000_000
                state = build_state(start_ms=start_ms)
                env = {
                    "BTC5M_PAPER_CLOSE_BUFFER_SEC": "0",
                    "BTC5M_PAPER_SCALE_IN_AFTER_SEC": "0,100,200",
                    "BTC5M_PAPER_STAGE_CONFIRM_SAMPLES": "1,1,1",
                }
                with patch.dict("os.environ", env, clear=False):
                    engine = BTC5mAdaptiveRoundPredictionEngine(Settings(), state, str(Path(directory) / "rounds.db"))
                with patch("polymarket_latency_bot.btc5m_hardened_round_prediction.now_ms", return_value=start_ms + 1000):
                    await engine.evaluate()
                refresh_signal(state, timestamp_ms=start_ms + 101000)
                with patch("polymarket_latency_bot.btc5m_hardened_round_prediction.now_ms", return_value=start_ms + 101000):
                    await engine.evaluate()
                payload = await state.snapshot()
                current = payload["paper_portfolio"]["current_round"]
                self.assertEqual(payload["orders_submitted"], 1)
                self.assertEqual(current["order_count"], 1)
                self.assertEqual(current["reason"], "insufficient_clean_sources")

        asyncio.run(run())

    def test_delayed_close_is_skipped_as_invalid_data(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                start_ms = 1_900_000_000_000
                state = build_state(start_ms=start_ms)
                env = {
                    "BTC5M_PAPER_CLOSE_BUFFER_SEC": "0",
                    "BTC5M_PAPER_SETTLEMENT_MAX_DELAY_MS": "2000",
                }
                with patch.dict("os.environ", env, clear=False):
                    engine = BTC5mAdaptiveRoundPredictionEngine(Settings(), state, str(Path(directory) / "rounds.db"))
                with patch("polymarket_latency_bot.btc5m_hardened_round_prediction.now_ms", return_value=start_ms + 1000):
                    await engine.evaluate()
                state.btc_prices.append((start_ms + 303000, 70100.0))
                await engine.settle_due_rounds()
                payload = await state.snapshot()
                summary = payload["paper_portfolio"]["summary"]
                self.assertEqual(summary["closed_trades"], 0)
                self.assertEqual(summary["skipped_wait"], 1)
                skipped = payload["paper_portfolio"]["skipped_rounds"][0]
                self.assertEqual(skipped["reason"], "invalid_btc_close_delayed")
                self.assertEqual(skipped["last_signal_quality"]["settlement_quality"], "invalid_data")

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
