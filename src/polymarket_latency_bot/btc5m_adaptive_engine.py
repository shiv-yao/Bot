from __future__ import annotations

import os
from typing import Any

from .btc5m_performance import build_paper_analytics
from .btc5m_round_prediction import BTC5mRoundPredictionEngine
from .models import now_ms


class BTC5mAdaptiveRoundPredictionEngine(BTC5mRoundPredictionEngine):
    """Wrap the guarded Paper engine with a conservative loss-streak cooldown.

    The base scale-in logic remains unchanged. After a configurable number of
    consecutive losing rounds, this wrapper temporarily pauses new evaluations.
    Existing Paper rounds continue to settle while the cooldown is active.
    Threshold auto-tuning intentionally remains disabled until enough Paper
    samples have been reviewed.
    """

    def __init__(self, settings: Any, state: Any, db_path: str | None = None) -> None:
        super().__init__(settings, state, db_path=db_path)
        self.cooldown_enabled = self._env_bool("BTC5M_PAPER_ADAPTIVE_COOLDOWN_ENABLED", True)
        self.cooldown_after_losses = max(1, int(os.getenv("BTC5M_PAPER_COOLDOWN_AFTER_LOSSES", "3")))
        self.cooldown_sec = max(1, int(os.getenv("BTC5M_PAPER_COOLDOWN_SEC", "900")))
        self.analytics_min_samples = max(1, int(os.getenv("BTC5M_PAPER_ANALYTICS_MIN_SAMPLES", "30")))
        self.cooldown_until_ms = 0
        self.cooldown_trigger_round: str | None = None

    async def _analytics(self) -> dict[str, Any]:
        snapshot = await self.state.snapshot()
        return build_paper_analytics(
            snapshot.get("paper_portfolio", {}),
            cooldown_after_losses=self.cooldown_after_losses,
            min_samples_for_review=self.analytics_min_samples,
        )

    async def _publish_adaptive_guard(self, analytics: dict[str, Any], timestamp_ms: int) -> None:
        active = self.cooldown_enabled and timestamp_ms < self.cooldown_until_ms
        async with self.state.lock:
            paper = dict(self.state.paper_portfolio or {})
            paper["analytics"] = analytics
            paper["adaptive_guard"] = {
                "cooldown_enabled": self.cooldown_enabled,
                "cooldown_active": active,
                "cooldown_after_losses": self.cooldown_after_losses,
                "cooldown_sec": self.cooldown_sec,
                "cooldown_until_ms": self.cooldown_until_ms if active else None,
                "cooldown_remaining_ms": max(0, self.cooldown_until_ms - timestamp_ms) if active else 0,
                "cooldown_trigger_round": self.cooldown_trigger_round,
                "auto_tuning_enabled": False,
                "analytics_min_samples": self.analytics_min_samples,
            }
            self.state.paper_portfolio = paper

    async def evaluate(self) -> None:
        timestamp = now_ms()
        analytics = await self._analytics()
        cooldown = analytics.get("cooldown", {}) or {}
        last_settled_round = cooldown.get("last_settled_round")
        should_start = (
            self.cooldown_enabled
            and bool(cooldown.get("recommended"))
            and bool(last_settled_round)
            and last_settled_round != self.cooldown_trigger_round
        )
        if should_start:
            self.cooldown_until_ms = timestamp + self.cooldown_sec * 1000
            self.cooldown_trigger_round = str(last_settled_round)

        active = self.cooldown_enabled and timestamp < self.cooldown_until_ms
        if active:
            prices = await self._btc_prices()
            if prices:
                await self.settle_due_rounds(prices)
            analytics = await self._analytics()
            self.last_reason = "adaptive_cooldown_active"
            await super().publish_state()
            await self._publish_adaptive_guard(analytics, timestamp)
            async with self.state.lock:
                previous = self.state.last_order_result or {}
                if previous.get("reason") != "adaptive_cooldown_active":
                    self.state.last_order_result = {
                        "mode": "btc_5m_prediction_market_paper_scale_in_adaptive_guarded",
                        "accepted": False,
                        "reason": "adaptive_cooldown_active",
                        "cooldown_until_ms": self.cooldown_until_ms,
                        "cooldown_trigger_round": self.cooldown_trigger_round,
                    }
            return

        await super().evaluate()
        analytics = await self._analytics()
        await self._publish_adaptive_guard(analytics, now_ms())
