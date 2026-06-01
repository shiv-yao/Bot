from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from time import time
from typing import Any

import aiohttp

from .config import Settings


@dataclass(slots=True)
class MarketSelection:
    slug: str
    condition_id: str
    yes_token_id: str
    no_token_id: str
    interval_start: int
    title: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _decode_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        decoded = json.loads(value)
        if isinstance(decoded, list):
            return [str(item) for item in decoded]
    raise ValueError("expected a JSON list")


def _market_from_payload(payload: Any, slug: str, interval_start: int) -> MarketSelection | None:
    markets = payload if isinstance(payload, list) else [payload]
    for market in markets:
        if not isinstance(market, dict):
            continue
        token_ids = _decode_list(market.get("clobTokenIds") or market.get("clob_token_ids"))
        outcomes = _decode_list(market.get("outcomes") or ["Up", "Down"])
        if len(token_ids) < 2:
            continue
        normalized = [outcome.strip().lower() for outcome in outcomes]
        up_index = next((i for i, value in enumerate(normalized) if value in {"up", "yes"}), 0)
        down_index = next((i for i, value in enumerate(normalized) if value in {"down", "no"}), 1)
        return MarketSelection(
            slug=slug,
            condition_id=str(market.get("conditionId") or market.get("condition_id") or ""),
            yes_token_id=token_ids[up_index],
            no_token_id=token_ids[down_index],
            interval_start=interval_start,
            title=str(market.get("question") or market.get("title") or slug),
        )
    return None


async def discover_current_market(session: aiohttp.ClientSession, settings: Settings) -> MarketSelection | None:
    interval = settings.market_interval_sec
    current_start = int(time()) // interval * interval
    for offset in (0, interval, -interval):
        interval_start = current_start + offset
        slug = f"{settings.market_slug_prefix}{interval_start}"
        async with session.get(
            f"{settings.gamma_api_url.rstrip('/')}/markets",
            params={"slug": slug},
            timeout=settings.market_discovery_timeout_sec,
        ) as response:
            response.raise_for_status()
            selection = _market_from_payload(await response.json(), slug, interval_start)
            if selection is not None:
                return selection
    return None
