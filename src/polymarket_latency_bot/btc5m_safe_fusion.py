from __future__ import annotations

from .multi_source import MultiSourceFusion


class BTC5mSafeFusion(MultiSourceFusion):
    """Add immediate neutralization when too few fresh BTC sources remain.

    The base fusion engine already neutralizes outlier and regime failures. This
    wrapper closes the remaining gap: when the fresh source count drops below the
    minimum, an older directional prediction is replaced with WAIT immediately
    rather than remaining actionable until its normal expiry.
    """

    async def _publish_fusion(self, timestamp: int) -> None:
        await super()._publish_fusion(timestamp)
        async with self.state.lock:
            status = str((self.state.fusion_snapshot or {}).get("status") or "")
        if status == "waiting_for_sources":
            await self._publish_neutral_prediction(timestamp, "waiting_for_sources")
