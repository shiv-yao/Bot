from __future__ import annotations

from .feeds import FeedHub
from .models import Prediction


class MeasuredFeedHub(FeedHub):
    async def upsert_prediction(self, prediction: Prediction) -> None:
        await self.state.record_event("prediction_update")
        await super().upsert_prediction(prediction)

    async def _handle_market_message(self, raw: str | bytes) -> None:
        await self.state.record_event("market_ws_update")
        await super()._handle_market_message(raw)
