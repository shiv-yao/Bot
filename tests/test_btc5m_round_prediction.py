from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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
    max_signal_age_ms: int = 1200
    min_contract_price: float = 0.10
    max_contract_price: float = 0.90
    max_spread: float = 0.06
    slippage_buffer: float = 0.002
    min_depth_multiple: float = 1.25
    depth_levels: int = 5


def set_books(state: BotState, *, timestamp_ms: int, yes_ask: float = 0.50, no_ask: float = 0.50) -> None:
    state.books["yes-token"] = BookTop(
        token_id="yes-token",
        best_bid=max(0.01, yes_ask - 0.01),
        best_ask=yes_ask,
        timestamp_ms=timestamp_ms,
        ask_levels=[{"price": yes_ask, "size": 1000.0}],
        bid_levels=[{"price": max(0.01, yes_ask - 0.01), "size": 1000.0}],
    )
    state.books["no-token"] = BookTop(
        token_id="no-token",
        best_bid=max(0.01, no_ask - 0.01),
        best_ask=no_ask,
        timestamp_ms=timestamp_ms,
        ask_levels=[{"price": no_ask, "size": 1000.0}],
        bid_levels=[{"price": max(0.01, no_ask - 0.01), "size": 1000.0}],
    )


def refresh_signal(
    state: BotState,
    *,
    timestamp_ms: int,
    probability_up: float,
    confidence: float,
    yes_ask: float = 0.50,
    no_ask: float = 0.50,
) -> None:
    state.predictions["multi_source_fusion"] = Prediction(
        source="multi_source_fusion",
        probability_up=probability_up,
        confidence=confidence,
        timestamp_ms=timestamp_ms,
    )
    set_books(state, timestamp_ms=timestamp_ms, yes_ask=yes_ask, no_ask=no_ask)


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
    refresh_signal(
        state,
        timestamp_ms=start_ms + 1000,
        probability_up=probability_up,
        confidence=confidence,
    )
    state.btc_prices.append((start_ms + 1000, 70000.0))
    return state


class BTC5mRoundPredictionTests(unittest.TestCase):
    def test_scale_in_uses_50_30_20_and_stops_after_three_entries(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                start_ms = 1_900_000_000_000
                state = build_state(start_ms=start_ms, probability_up=0.70, confidence=0.80)
                env = {
                    "BTC5M_PAPER_MAX_ROUND_NOTIONAL_USD": "100",
                    "BTC5M_PAPER_CLOSE_BUFFER_SEC": "0",
                    "BTC5M_PAPER_SCALE_IN_AFTER_SEC": "0,100,200",
                    "BTC5M_PAPER_SCALE_IN_WEIGHTS": "0.50,0.30,0.20",
                }
                with patch.dict("os.environ", env, clear=False):
                    engine = BTC5mRoundPredictionEngine(Settings(), state, str(Path(directory) / "rounds.db"))
                for timestamp in (start_ms + 1_000, start_ms + 101_000, start_ms + 201_000):
                    refresh_signal(state, timestamp_ms=timestamp, probability_up=0.70, confidence=0.80)
                    with patch("polymarket_latency_bot.btc5m_round_prediction.now_ms", return_value=timestamp):
                        await engine.evaluate()
                with patch("polymarket_latency_bot.btc5m_round_prediction.now_ms", return_value=start_ms + 202_000):
                    await engine.evaluate()
                payload = await state.snapshot()
                current = payload["paper_portfolio"]["current_round"]
                self.assertEqual(payload["orders_submitted"], 3)
                self.assertEqual(current["order_count"], 3)
                self.assertEqual(current["total_notional_usd"], 100.0)
                self.assertEqual([order["scale_stage"] for order in current["orders"]], [1, 2, 3])
                self.assertEqual([order["scale_weight"] for order in current["orders"]], [0.5, 0.3, 0.2])
                self.assertEqual([order["notional_usd"] for order in current["orders"]], [50.0, 30.0, 20.0])
                self.assertEqual(current["reason"], "scale_in_complete")

        asyncio.run(run())

    def test_direction_change_blocks_additional_scale_in(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                start_ms = 1_900_000_000_000
                state = build_state(start_ms=start_ms, probability_up=0.70, confidence=0.80)
                env = {
                    "BTC5M_PAPER_MAX_ROUND_NOTIONAL_USD": "100",
                    "BTC5M_PAPER_CLOSE_BUFFER_SEC": "0",
                    "BTC5M_PAPER_SCALE_IN_AFTER_SEC": "0,100,200",
                }
                with patch.dict("os.environ", env, clear=False):
                    engine = BTC5mRoundPredictionEngine(Settings(), state, str(Path(directory) / "rounds.db"))
                with patch("polymarket_latency_bot.btc5m_round_prediction.now_ms", return_value=start_ms + 1_000):
                    await engine.evaluate()
                refresh_signal(state, timestamp_ms=start_ms + 101_000, probability_up=0.30, confidence=0.80)
                with patch("polymarket_latency_bot.btc5m_round_prediction.now_ms", return_value=start_ms + 101_000):
                    await engine.evaluate()
                payload = await state.snapshot()
                current = payload["paper_portfolio"]["current_round"]
                self.assertEqual(payload["orders_submitted"], 1)
                self.assertEqual(current["order_count"], 1)
                self.assertEqual(current["reason"], "scale_in_direction_changed")

        asyncio.run(run())

    def test_stale_signal_is_rejected(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                start_ms = 1_900_000_000_000
                state = build_state(start_ms=start_ms, probability_up=0.70, confidence=0.80)
                env = {"BTC5M_PAPER_CLOSE_BUFFER_SEC": "0", "BTC5M_PAPER_MAX_SIGNAL_AGE_MS": "500"}
                with patch.dict("os.environ", env, clear=False):
                    engine = BTC5mRoundPredictionEngine(Settings(), state, str(Path(directory) / "rounds.db"))
                with patch("polymarket_latency_bot.btc5m_round_prediction.now_ms", return_value=start_ms + 2_000):
                    await engine.evaluate()
                payload = await state.snapshot()
                current = payload["paper_portfolio"]["current_round"]
                self.assertEqual(payload["orders_submitted"], 0)
                self.assertEqual(current["reason"], "signal_stale")

        asyncio.run(run())

    def test_low_net_edge_is_rejected(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                start_ms = 1_900_000_000_000
                state = build_state(start_ms=start_ms, probability_up=0.54, confidence=0.80)
                refresh_signal(state, timestamp_ms=start_ms + 1_000, probability_up=0.54, confidence=0.80, yes_ask=0.535)
                env = {
                    "BTC5M_PAPER_CLOSE_BUFFER_SEC": "0",
                    "BTC5M_PAPER_MIN_PROBABILITY_MARGIN": "0.01",
                    "BTC5M_PAPER_SCALE_IN_MIN_NET_EDGE": "0.008,0.012,0.018",
                }
                with patch.dict("os.environ", env, clear=False):
                    engine = BTC5mRoundPredictionEngine(Settings(), state, str(Path(directory) / "rounds.db"))
                with patch("polymarket_latency_bot.btc5m_round_prediction.now_ms", return_value=start_ms + 1_000):
                    await engine.evaluate()
                payload = await state.snapshot()
                current = payload["paper_portfolio"]["current_round"]
                self.assertEqual(payload["orders_submitted"], 0)
                self.assertEqual(current["reason"], "net_edge_too_low")

        asyncio.run(run())

    def test_yes_prediction_settles_as_win_when_btc_closes_higher(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as directory:
                start_ms = 1_900_000_000_000
                state = build_state(start_ms=start_ms, probability_up=0.70, confidence=0.80)
                env = {"BTC5M_PAPER_CLOSE_BUFFER_SEC": "0"}
                with patch.dict("os.environ", env, clear=False):
                    engine = BTC5mRoundPredictionEngine(Settings(), state, str(Path(directory) / "rounds.db"))
                with patch("polymarket_latency_bot.btc5m_round_prediction.now_ms", return_value=start_ms + 1_000):
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
                start_ms = 1_900_000_000_000
                state = build_state(start_ms=start_ms, probability_up=0.70, confidence=0.20)
                env = {"BTC5M_PAPER_CLOSE_BUFFER_SEC": "0"}
                with patch.dict("os.environ", env, clear=False):
                    engine = BTC5mRoundPredictionEngine(Settings(), state, str(Path(directory) / "rounds.db"))
                with patch("polymarket_latency_bot.btc5m_round_prediction.now_ms", return_value=start_ms + 1_000):
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
