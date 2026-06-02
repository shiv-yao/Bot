from __future__ import annotations

from .multi_source import MultiSourceFusion


class BTC5mSafeFusion(MultiSourceFusion):
    """Synchronize WAIT state whenever BTC fusion quality degrades.

    The base fusion engine already neutralizes outlier and regime failures. This
    wrapper also neutralizes insufficient-source states and keeps the fusion
    snapshot aligned with the shared WAIT prediction so APIs and dashboards do
    not accidentally display an older directional confidence.
    """

    async def _publish_neutral_prediction(self, timestamp: int, reason: str) -> None:
        await super()._publish_neutral_prediction(timestamp, reason)
        async with self.state.lock:
            snapshot = dict(self.state.fusion_snapshot or {})
            snapshot["probability_up"] = 0.5
            snapshot["confidence"] = 0.0
            snapshot["neutralized"] = True
            snapshot["neutralized_reason"] = reason
            snapshot["timestamp_ms"] = timestamp
            self.state.fusion_snapshot = snapshot

    async def _publish_fusion(self, timestamp: int) -> None:
        await super()._publish_fusion(timestamp)
        async with self.state.lock:
            status = str((self.state.fusion_snapshot or {}).get("status") or "")
        if status == "waiting_for_sources":
            await self._publish_neutral_prediction(timestamp, "waiting_for_sources")
