from __future__ import annotations

from time import time
from typing import Any

import aiohttp

from .feeds import FeedHub, _decode_list
from .models import Prediction


class MeasuredFeedHub(FeedHub):
    async def upsert_prediction(self, prediction: Prediction) -> None:
        await self.state.record_event("prediction_update")
        await super().upsert_prediction(prediction)

    async def _handle_market_message(self, raw: str | bytes) -> None:
        await self.state.record_event("market_ws_update")
        await super()._handle_market_message(raw)

    async def _discover_current_market(self, session: aiohttp.ClientSession) -> dict[str, Any] | None:
        interval = self.settings.market_interval_sec
        current = int(time() // interval * interval)
        prefixes = [
            self.settings.market_slug_prefix,
            "btc-updown-5m-",
            "bitcoin-updown-5m-",
            "bitcoin-up-or-down-5m-",
        ]
        seen: set[str] = set()
        for prefix in prefixes:
            if prefix in seen:
                continue
            seen.add(prefix)
            for timestamp in (current, current - interval):
                slug = f"{prefix}{timestamp}"
                async with session.get(f"{self.settings.gamma_api_url.rstrip('/')}/markets", params={"slug": slug}) as response:
                    response.raise_for_status()
                    payload = await response.json()
                if not payload:
                    continue
                market = payload[0] if isinstance(payload, list) else payload
                token_ids = _decode_list(market.get("clobTokenIds") or market.get("clob_token_ids"))
                outcomes = _decode_list(market.get("outcomes", ["Up", "Down"]))
                if len(token_ids) < 2:
                    continue
                self.settings.market_slug_prefix = prefix
                return {
                    "slug": slug,
                    "market_id": str(market.get("id", "")),
                    "condition_id": str(market.get("conditionId") or market.get("condition_id") or ""),
                    "yes_token_id": str(token_ids[0]),
                    "no_token_id": str(token_ids[1]),
                    "outcomes": [str(item) for item in outcomes],
                    "interval_start": timestamp,
                    "interval_sec": interval,
                    "question": str(market.get("question", "")),
                    "active": bool(market.get("active", True)),
                    "closed": bool(market.get("closed", False)),
                }
        return None
