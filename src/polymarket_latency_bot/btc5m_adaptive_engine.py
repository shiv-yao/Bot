from __future__ import annotations

import asyncio
import os
from typing import Any

from .btc5m_analytics_v4 import build_paper_analytics
from .btc5m_hardened_round_prediction import BTC5mHardenedRoundPredictionEngine
from .btc5m_storage_maintenance import BTC5mStorageMaintenance
from .models import now_ms


class BTC5mAdaptiveRoundPredictionEngine(BTC5mHardenedRoundPredictionEngine):
    """Run V4 hardened Paper scale-in with observational analytics only.

    Adaptive cooldown is intentionally disabled. Loss streaks, calibration,
    drift, walk-forward, EV and Shadow A/B diagnostics remain visible for
    review, but they do not pause new Paper evaluations or alter strategy
    thresholds automatically.
    """

    STRATEGY_NAME = "BTC_5M_EVENT_SCALE_IN_V4_HARDENED"

    def __init__(self, settings: Any, state: Any, db_path: str | None = None) -> None:
        super().__init__(settings, state, db_path=db_path)
        self.cooldown_enabled = False
        # Stage 1 may use a fresh RTDS fallback. Stages 2 and 3 remain strict.
        self.stage_min_clean_sources = (0, self.stage_min_clean_sources[1], self.stage_min_clean_sources[2])
        self.cooldown_after_losses = max(1, int(os.getenv("BTC5M_PAPER_COOLDOWN_AFTER_LOSSES", "3")))
        self.cooldown_sec = max(1, int(os.getenv("BTC5M_PAPER_COOLDOWN_SEC", "900")))
        self.analytics_min_samples = max(1, int(os.getenv("BTC5M_PAPER_ANALYTICS_MIN_SAMPLES", "30")))
        self.review_min_group_samples = max(1, int(os.getenv("BTC5M_PAPER_REVIEW_MIN_GROUP_SAMPLES", "10")))
        self.review_win_rate_threshold = min(1.0, max(0.0, float(os.getenv("BTC5M_PAPER_REVIEW_WIN_RATE_THRESHOLD", "0.45"))))
        self.rolling_window = max(1, int(os.getenv("BTC5M_PAPER_ROLLING_WINDOW", "30")))
        self.calibration_min_bucket_samples = max(1, int(os.getenv("BTC5M_PAPER_CALIBRATION_MIN_BUCKET_SAMPLES", "10")))
        self.overconfidence_gap_threshold = min(1.0, max(0.0, float(os.getenv("BTC5M_PAPER_OVERCONFIDENCE_GAP_THRESHOLD", "0.10"))))
        self.drift_min_samples = max(1, int(os.getenv("BTC5M_PAPER_DRIFT_MIN_SAMPLES", "20")))
        self.drift_win_rate_drop_threshold = min(1.0, max(0.0, float(os.getenv("BTC5M_PAPER_DRIFT_WIN_RATE_DROP_THRESHOLD", "0.15"))))
        self.drift_brier_increase_threshold = min(1.0, max(0.0, float(os.getenv("BTC5M_PAPER_DRIFT_BRIER_INCREASE_THRESHOLD", "0.10"))))
        self.walk_forward_train_min_samples = max(1, int(os.getenv("BTC5M_PAPER_WALK_FORWARD_TRAIN_MIN_SAMPLES", "30")))
        self.walk_forward_validation_samples = max(1, int(os.getenv("BTC5M_PAPER_WALK_FORWARD_VALIDATION_SAMPLES", "20")))
        self.walk_forward_win_rate_drop_threshold = min(1.0, max(0.0, float(os.getenv("BTC5M_PAPER_WALK_FORWARD_WIN_RATE_DROP_THRESHOLD", "0.10"))))
        self.walk_forward_brier_increase_threshold = min(1.0, max(0.0, float(os.getenv("BTC5M_PAPER_WALK_FORWARD_BRIER_INCREASE_THRESHOLD", "0.05"))))
        self.cooldown_until_ms = 0
        self.cooldown_trigger_round: str | None = None
        self.storage_maintenance = BTC5mStorageMaintenance(self.db_path)
        # Run once during startup so an already-full volume is handled promptly.
        self.storage_maintenance.maybe_run(force=True)

    def _record_shadow_decisions(self, item: Any, quality: dict[str, Any], notional: float) -> None:
        """Keep Shadow A/B observational data aligned with executable V4 quality."""

        stage = int(quality.get("stage") or 1)
        net_edge = float(quality.get("net_edge") or 0.0)
        min_net_edge = float(quality.get("min_net_edge") or 0.0)
        confirmation = dict(quality.get("confirmation") or {})
        if net_edge < min_net_edge or not bool(confirmation.get("confirmed")):
            return
        if item.orders:
            previous = item.orders[-1]
            entry_price = float(quality.get("estimated_vwap") or quality.get("entry_price") or 0.0)
            price_worsening = entry_price - float(previous.get("entry_price") or 0.0)
            edge_decay = float(previous.get("net_edge") or 0.0) - net_edge
            if price_worsening > self.stage_max_price_worsening[stage - 1]:
                return
            if edge_decay > self.stage_max_edge_decay[stage - 1]:
                return
        super()._record_shadow_decisions(item, quality, notional)

    async def _analytics(self) -> dict[str, Any]:
        snapshot = await self.state.snapshot()
        return build_paper_analytics(
            snapshot.get("paper_portfolio", {}),
            cooldown_after_losses=self.cooldown_after_losses,
            min_samples_for_review=self.analytics_min_samples,
            min_group_samples_for_review=self.review_min_group_samples,
            review_win_rate_threshold=self.review_win_rate_threshold,
            rolling_window=self.rolling_window,
            calibration_min_bucket_samples=self.calibration_min_bucket_samples,
            overconfidence_gap_threshold=self.overconfidence_gap_threshold,
            drift_min_samples=self.drift_min_samples,
            drift_win_rate_drop_threshold=self.drift_win_rate_drop_threshold,
            drift_brier_increase_threshold=self.drift_brier_increase_threshold,
            walk_forward_train_min_samples=self.walk_forward_train_min_samples,
            walk_forward_validation_samples=self.walk_forward_validation_samples,
            walk_forward_win_rate_drop_threshold=self.walk_forward_win_rate_drop_threshold,
            walk_forward_brier_increase_threshold=self.walk_forward_brier_increase_threshold,
        )

    async def _publish_adaptive_guard(self, analytics: dict[str, Any], timestamp_ms: int) -> None:
        active = False
        async with self.state.lock:
            paper = dict(self.state.paper_portfolio or {})
            rules = dict(paper.get("rules") or {})
            rules.update({
                "strategy": self.STRATEGY_NAME,
                "adaptive_cooldown_enabled": False,
                "cooldown_after_losses": self.cooldown_after_losses,
                "cooldown_sec": self.cooldown_sec,
                "auto_tuning_enabled": False,
                "analytics_min_samples": self.analytics_min_samples,
                "review_min_group_samples": self.review_min_group_samples,
                "review_win_rate_threshold": self.review_win_rate_threshold,
                "rolling_window": self.rolling_window,
                "calibration_min_bucket_samples": self.calibration_min_bucket_samples,
                "overconfidence_gap_threshold": self.overconfidence_gap_threshold,
                "drift_min_samples": self.drift_min_samples,
                "drift_win_rate_drop_threshold": self.drift_win_rate_drop_threshold,
                "drift_brier_increase_threshold": self.drift_brier_increase_threshold,
                "walk_forward_train_min_samples": self.walk_forward_train_min_samples,
                "walk_forward_validation_samples": self.walk_forward_validation_samples,
                "walk_forward_win_rate_drop_threshold": self.walk_forward_win_rate_drop_threshold,
                "walk_forward_brier_increase_threshold": self.walk_forward_brier_increase_threshold,
                "storage_retention_enabled": True,
                "storage_retention_rounds": self.storage_maintenance.retain_rounds,
                "storage_maintenance_interval_sec": self.storage_maintenance.maintenance_interval_sec,
            })
            paper["rules"] = rules
            paper["analytics"] = analytics
            paper["storage"] = self.storage_maintenance.get_report()
            paper["adaptive_guard"] = {
                "cooldown_enabled": False,
                "cooldown_active": active,
                "cooldown_after_losses": self.cooldown_after_losses,
                "cooldown_sec": self.cooldown_sec,
                "cooldown_until_ms": None,
                "cooldown_remaining_ms": 0,
                "cooldown_trigger_round": None,
                "auto_tuning_enabled": False,
                "analytics_min_samples": self.analytics_min_samples,
                "review_min_group_samples": self.review_min_group_samples,
                "review_win_rate_threshold": self.review_win_rate_threshold,
                "rolling_window": self.rolling_window,
                "calibration_min_bucket_samples": self.calibration_min_bucket_samples,
                "overconfidence_gap_threshold": self.overconfidence_gap_threshold,
                "drift_min_samples": self.drift_min_samples,
                "drift_win_rate_drop_threshold": self.drift_win_rate_drop_threshold,
                "drift_brier_increase_threshold": self.drift_brier_increase_threshold,
                "walk_forward_train_min_samples": self.walk_forward_train_min_samples,
                "walk_forward_validation_samples": self.walk_forward_validation_samples,
                "walk_forward_win_rate_drop_threshold": self.walk_forward_win_rate_drop_threshold,
                "walk_forward_brier_increase_threshold": self.walk_forward_brier_increase_threshold,
            }
            self.state.paper_portfolio = paper

    async def evaluate(self) -> None:
        await super().evaluate()
        await asyncio.to_thread(self.storage_maintenance.maybe_run)
        analytics = await self._analytics()
        await self._publish_adaptive_guard(analytics, now_ms())
